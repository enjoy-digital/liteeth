# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from liteeth.common import *
from liteeth.frontend.stream import LiteEthUDPStreamer

from targets.base import BaseSoC

# StreamSoC ----------------------------------------------------------------------------------------

class StreamSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address = 0x10e2d5000000,
            ip_address  = "192.168.1.50")
        self.submodules.streamer = LiteEthUDPStreamer(self.ethcore.udp, convert_ip("192.168.1.100"), 10000)
        self.comb += self.streamer.source.connect(self.streamer.sink)

# StreamSoDevel ------------------------------------------------------------------------------------

class StreamSoCDevel(StreamSoC):
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        StreamSoC.__init__(self, platform)
        analyzer_signals = [
            self.streamer.sink.valid,
            self.streamer.sink.ready,
            self.streamer.sink.data,

            self.streamer.source.valid,
            self.streamer.source.ready,
            self.streamer.source.data
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 4096, csr_csv="test/analyzer.csv")
        self.add_csr("analyzer")

default_subtarget = StreamSoC
