#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2016 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *
from liteeth.mac import sram

from litex.soc.interconnect import wishbone

# MAC Wishbone Interface ---------------------------------------------------------------------------

class LiteEthMACWishboneInterface(Module, AutoCSR):
    def __init__(self, dw, nrxslots=2, ntxslots=2, endianness="big", timestamp=None,
        rxslots_read_only  = True,
        txslots_write_only = False,
    ):
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))
        self.bus_rx = wishbone.Interface(data_width=dw)
        self.bus_tx = wishbone.Interface(data_width=dw)

        # # #

        # Storage in SRAM.
        # ----------------
        sram_depth = math.ceil(eth_mtu/(dw//8))
        self.submodules.sram = sram.LiteEthMACSRAM(dw, sram_depth, nrxslots, ntxslots, endianness, timestamp)
        self.comb += [
            self.sink.connect(self.sram.sink),
            self.sram.source.connect(self.source),
        ]

        # Ethernet RX Wishbone SRAM interfaces.
        # -------------------------------------

        # RX SRAMs.
        wb_rx_sram_ifs = []
        for n in range(nrxslots):
            wb_rx_sram_ifs.append(wishbone.SRAM(
                mem_or_size = self.sram.writer.mems[n],
                read_only   = rxslots_read_only,
                bus         = wishbone.Interface(data_width=dw)
            ))

        # Expose RX SRAMs on RX Bus.
        wb_slaves      = []
        decoderoffset  = log2_int(sram_depth, need_pow2=False)
        rx_decoderbits = log2_int(len(wb_rx_sram_ifs))
        for n, wb_sram_if in enumerate(wb_rx_sram_ifs):
            def slave_filter(a, v=n):
                return a[decoderoffset:decoderoffset+rx_decoderbits] == v
            wb_slaves.append((slave_filter, wb_sram_if.bus))
            self.submodules += wb_sram_if
        wb_con = wishbone.Decoder(self.bus_rx, wb_slaves, register=True)
        self.submodules += wb_con

        # Ethernet TX Wishbone SRAM interfaces.
        # -------------------------------------

        # TX SRAMs.
        wb_tx_sram_ifs = []
        for n in range(ntxslots):
            wb_tx_sram_ifs.append(wishbone.SRAM(
                mem_or_size = self.sram.reader.mems[n],
                write_only  = txslots_write_only,
                bus         = wishbone.Interface(data_width=dw)
            ))

        # Expose TX SRAMs on TX Bus.
        wb_slaves      = []
        decoderoffset  = log2_int(sram_depth, need_pow2=False)
        tx_decoderbits = log2_int(len(wb_tx_sram_ifs))
        for n, wb_sram_if in enumerate(wb_tx_sram_ifs):
            def slave_filter(a, v=n):
                return a[decoderoffset:decoderoffset+tx_decoderbits] == v
            wb_slaves.append((slave_filter, wb_sram_if.bus))
            self.submodules += wb_sram_if
        wb_con = wishbone.Decoder(self.bus_tx, wb_slaves, register=True)
        self.submodules += wb_con
