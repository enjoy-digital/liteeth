from liteeth.common import *
from liteeth.frontend.etherbone import LiteEthEtherbone

from targets.base import BaseSoC


class EtherboneSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address=0x10e2d5000000,
            ip_address="192.168.1.50")
        self.submodules.etherbone = LiteEthEtherbone(self.core.udp, 20000)
        self.add_wb_master(self.etherbone.master.bus)


class EtherboneSoCDevel(EtherboneSoC):
    csr_map = {
        "logic_analyzer": 20
    }
    csr_map.update(EtherboneSoC.csr_map)
    def __init__(self, platform):
        from litescope.frontend.logic_analyzer import LiteScopeLogicAnalyzer
        from litescope.core.port import LiteScopeTerm
        EtherboneSoC.__init__(self, platform)
        debug = (
            # mmap stream from HOST
            self.etherbone.master.sink.stb,
            self.etherbone.master.sink.sop,
            self.etherbone.master.sink.eop,
            self.etherbone.master.sink.ack,
            self.etherbone.master.sink.we,
            self.etherbone.master.sink.count,
            self.etherbone.master.sink.base_addr,
            self.etherbone.master.sink.be,
            self.etherbone.master.sink.addr,
            self.etherbone.master.sink.data,

            # mmap stream to HOST
            self.etherbone.master.source.stb,
            self.etherbone.master.source.sop,
            self.etherbone.master.source.eop,
            self.etherbone.master.source.ack,
            self.etherbone.master.source.we,
            self.etherbone.master.source.count,
            self.etherbone.master.source.base_addr,
            self.etherbone.master.source.be,
            self.etherbone.master.source.addr,
            self.etherbone.master.source.data,

            # etherbone wishbone master
            self.etherbone.master.bus.dat_w,
            self.etherbone.master.bus.dat_r,
            self.etherbone.master.bus.adr,
            self.etherbone.master.bus.sel,
            self.etherbone.master.bus.cyc,
            self.etherbone.master.bus.stb,
            self.etherbone.master.bus.ack,
            self.etherbone.master.bus.we,
            self.etherbone.master.bus.cti,
            self.etherbone.master.bus.bte,
            self.etherbone.master.bus.err
        )
        self.submodules.logic_analyzer = LiteScopeLogicAnalyzer(debug, 4096)
        self.logic_analyzer.trigger.add_port(LiteScopeTerm(self.logic_analyzer.dw))

    def do_exit(self, vns):
        self.logic_analyzer.export(vns, "test/logic_analyzer.csv")

default_subtarget = EtherboneSoC
