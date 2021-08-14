#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2016 Sebastien Bourdeauducq <sb@m-labs.hk>
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *
from liteeth.mac import sram

from litex.soc.interconnect import wishbone

# MAC Wishbone Interface ---------------------------------------------------------------------------

class LiteEthMACWishboneInterface(Module, AutoCSR):
    def __init__(self,
                 dw,
                 nrxslots=2,
                 ntxslots=2,
                 endianness="big",
                 timestamp=None,
                 wishbone_data_width=32):
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))
        self.bus    = wishbone.Interface()

        # # #

        # Ratio between Wishbone data width and SRAM data width,
        # required for calculating the proper address space length and
        # address decoding bits. Example: 64bit SRAM data width, 32bit
        # Wishbone data data width -> bus_ratio = 2
        bus_ratio = max(1, dw // wishbone_data_width)

        # Storage in SRAM
        sram_depth = math.ceil(eth_mtu / (dw // 8))
        self.submodules.sram = sram.LiteEthMACSRAM(dw, sram_depth, nrxslots, ntxslots, endianness, timestamp)
        self.comb += self.sink.connect(self.sram.sink)
        self.comb += self.sram.source.connect(self.source)

        # Wishbone SRAM interfaces and Wishbone data width converters
        # for the writer SRAM (i.e. Ethernet RX)
        wb_rx_sram_ifs = []
        for n in range(nrxslots):
            writer_sram_native_width = wishbone.SRAM(
                self.sram.writer.mems[n],
                read_only=True,
                bus = wishbone.Interface(data_width = dw)
            )
            self.submodules += writer_sram_native_width
            writer_sram_converted_width = wishbone.Interface(data_width = wishbone_data_width)
            writer_sram_converter = wishbone.Converter(writer_sram_converted_width, writer_sram_native_width.bus)
            self.submodules += writer_sram_converter
            wb_rx_sram_ifs += [writer_sram_converted_width]

        # Wishbone SRAM interfaces and Wishbone data width converters
        # for the reader SRAM (i.e. Ethernet TX)
        wb_tx_sram_ifs = []
        for n in range(nrxslots):
            reader_sram_native_width = wishbone.SRAM(
                self.sram.reader.mems[n],
                read_only=False,
                bus = wishbone.Interface(data_width = dw)
            )
            self.submodules += reader_sram_native_width
            reader_sram_converted_width = wishbone.Interface(data_width = wishbone_data_width)
            reader_sram_converter = wishbone.Converter(reader_sram_converted_width, reader_sram_native_width.bus)
            self.submodules += reader_sram_converter
            wb_tx_sram_ifs += [reader_sram_converted_width]

        self.wb_sram_ifs = wb_sram_ifs = wb_rx_sram_ifs + wb_tx_sram_ifs

        wb_slaves = []
        decoderoffset = log2_int(sram_depth * bus_ratio, need_pow2=False)
        rx_decoderbits   = log2_int(len(wb_rx_sram_ifs))
        tx_decoderbits   = log2_int(len(wb_tx_sram_ifs))
        decoderbits      = max(rx_decoderbits, tx_decoderbits)+1
        for n, wb_sram_if in enumerate(wb_sram_ifs):
            def slave_filter(a, v=n):
                return a[decoderoffset:decoderoffset+decoderbits] == v
            wb_slaves.append((slave_filter, wb_sram_if))

        wb_con = wishbone.Decoder(self.bus, wb_slaves, register=True)
        self.submodules += wb_con
