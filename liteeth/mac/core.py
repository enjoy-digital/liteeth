#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.mac import gap, preamble, crc, padding, last_be
from liteeth.phy.model import LiteEthPHYModel

from migen.genlib.cdc import PulseSynchronizer

from litex.soc.interconnect.stream import BufferizeEndpoints, DIR_SOURCE, DIR_SINK

# MAC Core -----------------------------------------------------------------------------------------

class LiteEthMACCore(Module, AutoCSR):
    def __init__(self, phy, dw,
                 with_sys_datapath = False,
                 with_preamble_crc = True,
                 with_padding      = True):

        # Endpoints.
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # Parameters.
        core_dw = dw
        if core_dw < phy.dw:
            raise ValueError("Core data width({}) must be larger than PHY data width({})".format(core_dw, phy.dw))

        if with_sys_datapath:
            cd_tx = "sys"
            cd_rx = "sys"
            dw = core_dw
        else:
            cd_tx = "eth_tx"
            cd_rx = "eth_rx"
            dw = phy.dw

        # TX Data-Path (Core --> PHY).
        # ------------------------------------------------------------------------------------------
        self.tx_datapath = tx_datapath = [phy]

        # Early sys conversion.
        if with_sys_datapath:
            self.add_tx_converter(core_dw, phy.dw)

        # Interpacket gap
        tx_gap_inserter = gap.LiteEthMACGap(dw)
        tx_gap_inserter = ClockDomainsRenamer(cd_tx)(tx_gap_inserter)
        self.submodules += tx_gap_inserter
        tx_datapath.append(tx_gap_inserter)

        # Preamble / CRC
        if isinstance(phy, LiteEthPHYModel):
            # In simulation, avoid CRC/Preamble to enable direct connection to the Ethernet tap.
            self._preamble_crc = CSRStatus(reset=1)
        elif with_preamble_crc:
            self._preamble_crc = CSRStatus(reset=1)

            # Preamble insert.
            preamble_inserter = preamble.LiteEthMACPreambleInserter(dw)
            preamble_inserter = ClockDomainsRenamer(cd_tx)(preamble_inserter)
            self.submodules += preamble_inserter
            tx_datapath.append(preamble_inserter)

            # CRC insert.
            crc32_inserter = crc.LiteEthMACCRC32Inserter(eth_phy_description(dw))
            crc32_inserter = BufferizeEndpoints({"sink": DIR_SINK})(crc32_inserter)
            crc32_inserter = ClockDomainsRenamer(cd_tx)(crc32_inserter)
            self.submodules += crc32_inserter
            tx_datapath.append(crc32_inserter)

        # Padding
        if with_padding:
            padding_inserter = padding.LiteEthMACPaddingInserter(dw, 60)
            padding_inserter = ClockDomainsRenamer(cd_tx)(padding_inserter)
            self.submodules += padding_inserter
            tx_datapath.append(padding_inserter)

        # Late sys conversion.
        if not with_sys_datapath:
            self.add_tx_converter(core_dw, phy.dw)

        # Data-Path.
        self.submodules.tx_pipeline = stream.Pipeline(*reversed(tx_datapath))
        self.comb += self.sink.connect(self.tx_pipeline.sink)

        # RX Data-Path (PHY --> Core).
        # ------------------------------------------------------------------------------------------
        self.rx_datapath = rx_datapath = [phy]

        # Early sys conversion.
        if with_sys_datapath:
            self.add_rx_converter(core_dw, phy.dw)

        # Preamble / CRC
        if with_preamble_crc:
            # Preamble check.
            preamble_checker = preamble.LiteEthMACPreambleChecker(dw)
            preamble_checker = ClockDomainsRenamer(cd_rx)(preamble_checker)
            self.submodules += preamble_checker
            rx_datapath.append(preamble_checker)

            # Preamble error counter.
            self.submodules.ps_preamble_error = PulseSynchronizer(cd_rx, "sys")
            self.preamble_errors = CSRStatus(32)
            self.comb += self.ps_preamble_error.i.eq(preamble_checker.error)
            self.sync += [
                If(self.ps_preamble_error.o,
                    self.preamble_errors.status.eq(self.preamble_errors.status + 1))
            ]

            # CRC check.
            crc32_checker = crc.LiteEthMACCRC32Checker(eth_phy_description(dw))
            crc32_checker = BufferizeEndpoints({"sink": DIR_SINK})(crc32_checker)
            crc32_checker = ClockDomainsRenamer(cd_rx)(crc32_checker)
            self.submodules += crc32_checker
            rx_datapath.append(crc32_checker)

            # CRC error counter.
            self.crc_errors = CSRStatus(32)
            self.submodules.ps_crc_error = PulseSynchronizer(cd_rx, "sys")
            self.comb += self.ps_crc_error.i.eq(crc32_checker.error),
            self.sync += [
                If(self.ps_crc_error.o,
                    self.crc_errors.status.eq(self.crc_errors.status + 1)
                )
            ]

        # Padding.
        if with_padding:
            padding_checker = padding.LiteEthMACPaddingChecker(dw, 60)
            padding_checker = ClockDomainsRenamer(cd_rx)(padding_checker)
            self.submodules += padding_checker
            rx_datapath.append(padding_checker)

        # Late sys conversion.
        if not with_sys_datapath:
            self.add_rx_converter(core_dw, phy.dw)

        # Data-Path.
        self.submodules.rx_pipeline = stream.Pipeline(*rx_datapath)
        self.comb += self.rx_pipeline.source.connect(self.source)

    def add_tx_converter(self, dw, phy_dw):
        # Delimiters.
        if dw != 8:
            tx_last_be = last_be.LiteEthMACTXLastBE(phy_dw)
            tx_last_be = ClockDomainsRenamer("eth_tx")(tx_last_be)
            self.submodules += tx_last_be
            self.tx_datapath.append(tx_last_be)

        # Converters.
        if dw != phy_dw:
            tx_converter = stream.StrideConverter(
                description_from = eth_phy_description(dw),
                description_to   = eth_phy_description(phy_dw))
            tx_converter = ClockDomainsRenamer("eth_tx")(tx_converter)
            self.submodules += tx_converter
            self.tx_datapath.append(tx_converter)

        # Cross Domain Crossing.
        tx_cdc = stream.ClockDomainCrossing(eth_phy_description(dw),
            cd_from = "sys",
            cd_to   = "eth_tx",
            depth   = 32)
        self.submodules += tx_cdc
        self.tx_datapath.append(tx_cdc)

    def add_rx_converter(self, dw, phy_dw):
        # Delimiters.
        if dw != 8:
            rx_last_be = last_be.LiteEthMACRXLastBE(phy_dw)
            rx_last_be = ClockDomainsRenamer("eth_rx")(rx_last_be)
            self.submodules += rx_last_be
            self.rx_datapath.append(rx_last_be)

        # Converters.
        if dw != phy_dw:
            rx_converter = stream.StrideConverter(
                description_from = eth_phy_description(phy_dw),
                description_to   = eth_phy_description(dw))
            rx_converter = ClockDomainsRenamer("eth_rx")(rx_converter)
            self.submodules += rx_converter
            self.rx_datapath.append(rx_converter)

        # Cross Domain Crossing
        rx_cdc = stream.ClockDomainCrossing(eth_phy_description(dw),
            cd_from = "eth_rx",
            cd_to   = "sys",
            depth   = 32)
        self.submodules += rx_cdc
        self.rx_datapath.append(rx_cdc)
