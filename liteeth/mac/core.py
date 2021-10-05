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
        self.tx_datapath = tx_datapath = []

        # Sink.
        tx_datapath.append(self.sink)

        # Late sys conversion.
        if not with_sys_datapath:
            self.add_tx_converter(core_dw, phy.dw)

        # Padding
        if with_padding:
            tx_padding = padding.LiteEthMACPaddingInserter(dw, 60)
            tx_padding = ClockDomainsRenamer(cd_tx)(tx_padding)
            self.submodules += tx_padding
            tx_datapath.append(tx_padding)

        # Preamble / CRC
        if isinstance(phy, LiteEthPHYModel):
            # In simulation, avoid CRC/Preamble to enable direct connection to the Ethernet tap.
            self._preamble_crc = CSRStatus(reset=1)
        elif with_preamble_crc:
            self._preamble_crc = CSRStatus(reset=1)
            # CRC insert.
            tx_crc = crc.LiteEthMACCRC32Inserter(eth_phy_description(dw))
            tx_crc = BufferizeEndpoints({"sink": DIR_SINK})(tx_crc) # FIXME: Still required?
            tx_crc = ClockDomainsRenamer(cd_tx)(tx_crc)
            self.submodules += tx_crc
            tx_datapath.append(tx_crc)

            # Preamble insert.
            tx_preamble = preamble.LiteEthMACPreambleInserter(dw)
            tx_preamble = ClockDomainsRenamer(cd_tx)(tx_preamble)
            self.submodules += tx_preamble
            tx_datapath.append(tx_preamble)

        # Interpacket gap
        tx_gap = gap.LiteEthMACGap(dw)
        tx_gap = ClockDomainsRenamer(cd_tx)(tx_gap)
        self.submodules += tx_gap
        tx_datapath.append(tx_gap)

        # Early sys conversion.
        if with_sys_datapath:
            self.add_tx_converter(core_dw, phy.dw)

        # PHY.
        tx_datapath.append(phy)

        # Data-Path.
        self.submodules.tx_pipeline = stream.Pipeline(*tx_datapath)

        # RX Data-Path (PHY --> Core).
        # ------------------------------------------------------------------------------------------
        self.rx_datapath = rx_datapath = []

        # PHY.
        rx_datapath.append(phy)

        # Early sys conversion.
        if with_sys_datapath:
            self.add_rx_converter(core_dw, phy.dw)

        # Preamble / CRC
        if with_preamble_crc:
            # Preamble check.
            rx_preamble = preamble.LiteEthMACPreambleChecker(dw)
            rx_preamble = ClockDomainsRenamer(cd_rx)(rx_preamble)
            self.submodules += rx_preamble
            rx_datapath.append(rx_preamble)

            # Preamble error counter.
            self.submodules.ps_preamble_error = PulseSynchronizer(cd_rx, "sys")
            self.preamble_errors = CSRStatus(32)
            self.comb += self.ps_preamble_error.i.eq(rx_preamble.error)
            self.sync += [
                If(self.ps_preamble_error.o,
                    self.preamble_errors.status.eq(self.preamble_errors.status + 1))
            ]

            # CRC check.
            rx_crc = crc.LiteEthMACCRC32Checker(eth_phy_description(dw))
            rx_crc = BufferizeEndpoints({"sink": DIR_SINK})(rx_crc) # FIXME: Still required?
            rx_crc = ClockDomainsRenamer(cd_rx)(rx_crc)
            self.submodules += rx_crc
            rx_datapath.append(rx_crc)

            # CRC error counter.
            self.crc_errors = CSRStatus(32)
            self.submodules.ps_crc_error = PulseSynchronizer(cd_rx, "sys")
            self.comb += self.ps_crc_error.i.eq(rx_crc.error),
            self.sync += [
                If(self.ps_crc_error.o,
                    self.crc_errors.status.eq(self.crc_errors.status + 1)
                )
            ]

        # Padding.
        if with_padding:
            rx_padding = padding.LiteEthMACPaddingChecker(dw, 60)
            rx_padding = ClockDomainsRenamer(cd_rx)(rx_padding)
            self.submodules += rx_padding
            rx_datapath.append(rx_padding)

        # Late sys conversion.
        if not with_sys_datapath:
            self.add_rx_converter(core_dw, phy.dw)

        # Source.
        rx_datapath.append(self.source)

        # Data-Path.
        self.submodules.rx_pipeline = stream.Pipeline(*rx_datapath)

    def add_tx_converter(self, dw, phy_dw):
        # Cross Domain Crossing.
        tx_cdc = stream.ClockDomainCrossing(eth_phy_description(dw),
            cd_from = "sys",
            cd_to   = "eth_tx",
            depth   = 32)
        self.submodules += tx_cdc
        self.tx_datapath.append(tx_cdc)

        # Converters.
        if dw != phy_dw:
            tx_converter = stream.StrideConverter(
                description_from = eth_phy_description(dw),
                description_to   = eth_phy_description(phy_dw))
            tx_converter = ClockDomainsRenamer("eth_tx")(tx_converter)
            self.submodules += tx_converter
            self.tx_datapath.append(tx_converter)

        # Delimiters.
        if dw != 8:
            tx_last_be = last_be.LiteEthMACTXLastBE(phy_dw)
            tx_last_be = ClockDomainsRenamer("eth_tx")(tx_last_be)
            self.submodules += tx_last_be
            self.tx_datapath.append(tx_last_be)

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
