from liteeth.common import *
from liteeth.mac.common import *
from liteeth.mac.core import LiteEthMACCore
from liteeth.mac.wishbone import LiteEthMACWishboneInterface


class LiteEthMAC(Module, AutoCSR):
    def __init__(self, phy, dw,
                 interface="crossbar",
                 endianness="big",
                 with_preamble_crc=True,
                 nrxslots=2,
                 ntxslots=2):
        self.submodules.core = LiteEthMACCore(phy, dw, endianness, with_preamble_crc)
        self.csrs = []
        if interface == "crossbar":
            self.submodules.crossbar = LiteEthMACCrossbar(dw)
            self.submodules.packetizer = LiteEthMACPacketizer(dw)
            self.submodules.depacketizer = LiteEthMACDepacketizer(dw)
            self.comb += [
                self.crossbar.master.source.connect(self.packetizer.sink),
                self.packetizer.source.connect(self.core.sink),
                self.core.source.connect(self.depacketizer.sink),
                self.depacketizer.source.connect(self.crossbar.master.sink)
            ]
        elif interface == "wishbone":
            self.rx_slots = CSRConstant(nrxslots)
            self.tx_slots = CSRConstant(ntxslots)
            self.slot_size = CSRConstant(2**bits_for(eth_mtu))
            self.submodules.interface = LiteEthMACWishboneInterface(dw, nrxslots, ntxslots, endianness)
            self.comb += Port.connect(self.interface, self.core)
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
        else:
            raise NotImplementedError

    def get_csrs(self):
        return self.csrs
