# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from liteeth.common import *
from liteeth.frontend.tty import LiteEthTTY

from targets.base import BaseSoC

# TTYSoC -------------------------------------------------------------------------------------------

class TTYSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address=0x10e2d5000000,
            ip_address="192.168.1.50")
        self.submodules.tty = LiteEthTTY(self.ethcore.udp, convert_ip("192.168.1.100"), 10000)
        self.comb += self.tty.source.connect(self.tty.sink)

# TTYSoDevel ---------------------------------------------------------------------------------------

class TTYSoCDevel(TTYSoC):
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        TTYSoC.__init__(self, platform)
        analyzer_signals = [
            self.tty.sink.valid,
            self.tty.sink.ready,
            self.tty.sink.data,

            self.tty.source.valid,
            self.tty.source.ready,
            self.tty.source.data
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 4096, csr_csv="test/analyzer.csv")
        self.add_csr("analyzer")

default_subtarget = TTYSoC
