from migen import *

from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

from .writer import LiteEthMACDMAWriter
from .reader import LiteEthMACDMAReader


class LiteEthMACDMAInterface(Module, AutoCSR):
    def __init__(self, dw, write_port, read_port, offset, nrxslots, ntxslots, slot_size):
        write_offset = offset
        read_offset  = offset + slot_size * nrxslots

        self.submodules.sram_writer = LiteEthMACDMAWriter(dw, nrxslots, slot_size, write_port, write_offset)
        self.submodules.sram_reader = LiteEthMACDMAReader(dw, ntxslots, slot_size, read_port, read_offset)
        self.submodules.ev     = SharedIRQ(self.sram_writer.ev, self.sram_reader.ev)
        self.sink, self.source = self.sram_writer.sink, self.sram_reader.source
