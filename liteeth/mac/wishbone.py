#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2016 Sebastien Bourdeauducq <sb@m-labs.hk>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.mac import sram

from litex.soc.interconnect import wishbone

# MAC Wishbone Interface ---------------------------------------------------------------------------

class LiteEthMACWishboneInterface(Module, AutoCSR):
    def __init__(self, dw, nrxslots=2, ntxslots=2, endianness="big"):
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))
        self.bus    = wishbone.Interface()

        # # #

        # storage in SRAM
        sram_depth = eth_mtu//(dw//8)
        self.submodules.sram = sram.LiteEthMACSRAM(dw, sram_depth, nrxslots, ntxslots, endianness)
        self.comb += [
            self.sink.connect(self.sram.sink),
            self.sram.source.connect(self.source)
        ]

        # Wishbone interface
        wb_rx_sram_ifs = [wishbone.SRAM(self.sram.writer.mems[n], read_only=True)
            for n in range(nrxslots)]
        wb_tx_sram_ifs = [wishbone.SRAM(self.sram.reader.mems[n], read_only=False)
            for n in range(ntxslots)]
        wb_sram_ifs = wb_rx_sram_ifs + wb_tx_sram_ifs

        wb_slaves     = []
        decoderoffset = log2_int(sram_depth, need_pow2=False)
        decoderbits   = log2_int(len(wb_sram_ifs))
        for n, wb_sram_if in enumerate(wb_sram_ifs):
            def slave_filter(a, v=n):
                return a[decoderoffset:decoderoffset+decoderbits] == v
            wb_slaves.append((slave_filter, wb_sram_if.bus))
            self.submodules += wb_sram_if
        wb_con = wishbone.Decoder(self.bus, wb_slaves, register=True)
        self.submodules += wb_con
