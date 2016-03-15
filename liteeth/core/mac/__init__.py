from liteeth.common import *
from liteeth.core.mac.common import *
from liteeth.core.mac.core import LiteEthMACCore
from liteeth.core.mac.wishbone import LiteEthMACWishboneInterface


class LiteEthMAC(Module, AutoCSR):
    def __init__(self, phy, dw,
                 interface="crossbar",
                 endianness="big",
                 with_preamble_crc=True):
        self.submodules.core = LiteEthMACCore(phy, dw, endianness, with_preamble_crc)
        self.csrs = []
        if interface == "crossbar":
            self.submodules.crossbar = LiteEthMACCrossbar()
            self.submodules.packetizer = LiteEthMACPacketizer()
            self.submodules.depacketizer = LiteEthMACDepacketizer()
            self.comb += [
                self.crossbar.master.source.connect(self.packetizer.sink),
                self.packetizer.source.connect(self.core.sink),
                self.core.source.connect(self.depacketizer.sink),
                self.depacketizer.source.connect(self.crossbar.master.sink)
            ]
        elif interface == "wishbone":
            self.submodules.interface = LiteEthMACWishboneInterface(dw, 2, 2)
            self.comb += Port.connect(self.interface, self.core)
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
        else:
            raise NotImplementedError

    def get_csrs(self):
        return self.csrs
