# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from liteeth.common import *

from targets.base import BaseSoC

# UDPSoC -------------------------------------------------------------------------------------------

class UDPSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address=0x10e2d5000000,
            ip_address="192.168.1.50")

        # add udp loopback on port 6000 with dw=8
        self.add_udp_loopback(6000, 8,  8192, "loopback_8")
        # add udp loopback on port 8000 with dw=32
        self.add_udp_loopback(8000, 32, 8192, "loopback_32")

    def add_udp_loopback(self, port, dw, depth, name=None):
        port = self.ethcore.udp.crossbar.get_port(port, dw)
        buf = stream.SyncFIFO(eth_udp_user_description(dw), depth//(dw//8))
        if name is None:
            self.submodules += buf
        else:
            setattr(self.submodules, name, buf)
        self.comb += port.source.connect(buf.sink)
        self.comb += buf.source.connect(port.sink)

# UDPSoCDevel --------------------------------------------------------------------------------------

class UDPSoCDevel(UDPSoC):
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        UDPSoC.__init__(self, platform)
        analyzer_signals = [
            self.loopback_8.sink.valid,
            self.loopback_8.sink.last,
            self.loopback_8.sink.ready,
            self.loopback_8.sink.data,

            self.loopback_8.source.valid,
            self.loopback_8.source.last,
            self.loopback_8.source.ready,
            self.loopback_8.source.data,

            self.loopback_32.sink.valid,
            self.loopback_32.sink.last,
            self.loopback_32.sink.ready,
            self.loopback_32.sink.data,

            self.loopback_32.source.valid,
            self.loopback_32.source.last,
            self.loopback_32.source.ready,
            self.loopback_32.source.data
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 4096, csr_csv="test/analyzer.csv")
        self.add_csr("analyzer")

default_subtarget = UDPSoC
