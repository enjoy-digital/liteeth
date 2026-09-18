from litex.gen import *

from liteeth.common import *

from collections import OrderedDict

from litex.soc.interconnect.stream import SyncFIFO, Endpoint

from litex.soc.interconnect.packet import Dispatcher

class LiteEthIBCQMasterPort:
    def __init__(self):
        self.sink = Endpoint(eth_rocev2_cq_description())

class LiteEthIBCQSlavePort:
    def __init__(self):
        self.source = Endpoint(eth_rocev2_cq_description())

class LiteEthIBCQUserPort(LiteEthIBCQSlavePort):
    def __init__(self):
        LiteEthIBCQSlavePort.__init__(self)

class LiteEthIBCQCrossbar(LiteXModule):
    def __init__(self):
        self.users = OrderedDict()
        self.master = LiteEthIBCQMasterPort()

    def get_port(self, qp_id, cd="sys", depth=None):
        if qp_id in self.users.keys():
            raise ValueError(f"QP with id {qp_id:#x} already assigned")

        user_port     = LiteEthIBCQUserPort()
        internal_port = LiteEthIBCQUserPort()

        self.cdc = cdc = stream.ClockDomainCrossing(
            layout  = eth_rocev2_cq_description(),
            cd_from = "sys",
            cd_to   = cd,
            depth   = depth,
        )
        self.comb += [
            internal_port.source.connect(cdc.sink),
            cdc.source.connect(user_port.source)
        ]

        self.users[qp_id] = internal_port
        return user_port

    def do_finalize(self):
        assert len(self.users) == 1

        self.comb += self.master.sink.connect(list(self.users.values())[0].source)
        # sources = [port.source for port in self.users.values()]
        # self.dispatcher = Dispatcher(self.master.sink, sources)
        # dispatch_sig = getattr(self.master.sink, self.dispatch_param)
        # for i, k in enumerate(self.users.keys()):
        #     self.comb += If(dispatch_sig == k, self.dispatcher.sel.eq(2**i))

# Completion queue
class LiteEthCQ(LiteXModule):
    def __init__(self, depth, buffered=True):
        self.sink     = sink     = Endpoint(eth_rocev2_cq_description())
        self.crossbar = crossbar = LiteEthIBCQCrossbar()

        # # #

        fifo = SyncFIFO(eth_rocev2_cq_description(), depth, buffered)
        self.submodules += fifo

        self.comb += [
            sink.connect(fifo.sink),
            fifo.source.connect(crossbar.master.sink)
        ]
