#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from collections import OrderedDict

from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.packet import Arbiter, Dispatcher

# Crossbar -----------------------------------------------------------------------------------------

class LiteEthCrossbar(LiteXModule):
    def __init__(self, master_port, dispatch_param, dw=8, with_pipelining=False):
        self.users  = OrderedDict()
        self.with_pipelining = with_pipelining
        self.master = master_port(dw)
        self.dispatch_param = dispatch_param

    # overload this in derived classes
    def get_port(self, *args, **kwargs):
        pass

    def do_finalize(self):
        # TX arbitrate
        sinks = [port.sink for port in self.users.values()]
        self.arbiter = Arbiter(sinks, self.master.source)

        # RX dispatch
        # Buffered because the Dispatcher's select cone is combinational in both directions,
        # putting the whole arbitration between master sink and each user's FIFO write enable.
        sources = []
        for i, port in enumerate(self.users.values()):
            if not self.with_pipelining:
                sources.append(port.source)
                continue
            buffer = stream.Buffer(port.source.description, pipe_valid=True, pipe_ready=True)
            setattr(self, f"rx_buffer{i}", buffer)
            self.comb += buffer.source.connect(port.source)
            sources.append(buffer.sink)
        self.dispatcher = Dispatcher(self.master.sink, sources, one_hot=True)
        dispatch_sig = getattr(self.master.sink, self.dispatch_param)
        for i, (k, v) in enumerate(self.users.items()):
            self.comb += If(dispatch_sig == k, self.dispatcher.sel.eq(2**i))
