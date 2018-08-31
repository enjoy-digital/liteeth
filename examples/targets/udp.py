from liteeth.common import *

from targets.base import BaseSoC


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
        port = self.core.udp.crossbar.get_port(port, dw)
        buf = stream.SyncFIFO(eth_udp_user_description(dw), depth//(dw//8))
        if name is None:
            self.submodules += buf
        else:
            setattr(self.submodules, name, buf)
        self.comb += Port.connect(port, buf)


class UDPSoCDevel(UDPSoC):
    csr_map = {
        "analyzer": 20
    }
    csr_map.update(UDPSoC.csr_map)
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        UDPSoC.__init__(self, platform)
        debug = [
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
        self.submodules.analyzer = LiteScopeAnalyzer(debug, 4096)

    def do_exit(self, vns):
        self.analyzer.export_csv(vns, "test/analyzer.csv")

default_subtarget = UDPSoC
