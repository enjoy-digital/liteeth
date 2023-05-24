#
# This file is part of LiteEth.
#
# Copyright (c) 2015 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from collections import OrderedDict

from liteeth.common import *

from litex.soc.interconnect.packet import Arbiter, Dispatcher

# Crossbar -----------------------------------------------------------------------------------------

class LiteEthCrossbar(Module):
    def __init__(self, master_port, dispatch_param, dw=8):
        self.users  = OrderedDict()
        self.master = master_port(dw)
        self.dispatch_param = dispatch_param

    # overload this in derived classes
    def get_port(self, *args, **kwargs):
        pass

    def do_finalize(self):
        # TX arbitrate
        sinks = [port.sink for port in self.users.values()]
        self.submodules.arbiter = Arbiter(sinks, self.master.source)

        # RX dispatch
        sources = [port.source for port in self.users.values()]
        self.submodules.dispatcher = Dispatcher(self.master.sink, sources, one_hot=True)
        if type(self.dispatch_param) is list:
            params = [getattr(self.master.sink, param) for param in self.dispatch_param]
            dispatch_sig = Cat(*params)
        else:
            dispatch_sig = getattr(self.master.sink, self.dispatch_param)
        for i, (k, v) in enumerate(self.users.items()):
            self.comb += If(dispatch_sig == k, self.dispatcher.sel.eq(2**i))
