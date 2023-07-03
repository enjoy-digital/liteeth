#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V. <goemansrowan@gmail.com>
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
                 with_padding      = True,
                 tx_cdc_depth      = 32,
                 tx_cdc_buffered   = False,
                 rx_cdc_depth      = 32,
                 rx_cdc_buffered   = False,
                 ):

        # Endpoints.
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # Parameters.
        core_dw = dw
        phy_dw  = phy.dw
        if core_dw < phy_dw:
            raise ValueError("Core data width({}) must be larger than PHY data width({})".format(core_dw, phy_dw))
        if with_sys_datapath:
            cd_tx       = "sys"
            cd_rx       = "sys"
            datapath_dw = core_dw
        else:
            cd_tx       = "eth_tx"
            cd_rx       = "eth_rx"
            datapath_dw = phy_dw
        if isinstance(phy, LiteEthPHYModel):
            with_preamble_crc = False # Disable Preamble/CRC with PHY Model for direct connection to the Ethernet tap.

        # CSRs.
        if with_preamble_crc:
            self.preamble_crc = CSRStatus(reset=1)

        # TX Data-Path (Core --> PHY).
        # ------------------------------------------------------------------------------------------
        class TXDatapath(Module, AutoCSR):
            def __init__(self):
                self.pipeline = []

            def add_cdc(self):
                tx_cdc = stream.ClockDomainCrossing(eth_phy_description(core_dw),
                    cd_from = "sys",
                    cd_to   = "eth_tx",
                    depth   = tx_cdc_depth,
                    buffered = tx_cdc_buffered
                    )
                self.submodules += tx_cdc
                self.pipeline.append(tx_cdc)

            def add_converter(self):
                tx_converter = stream.StrideConverter(
                    description_from = eth_phy_description(core_dw),
                    description_to   = eth_phy_description(phy_dw))
                tx_converter = ClockDomainsRenamer("eth_tx")(tx_converter)
                self.submodules += tx_converter
                self.pipeline.append(tx_converter)

            def add_last_be(self):
                tx_last_be = last_be.LiteEthMACTXLastBE(phy_dw)
                tx_last_be = ClockDomainsRenamer("eth_tx")(tx_last_be)
                self.submodules += tx_last_be
                self.pipeline.append(tx_last_be)

            def add_padding(self):
                tx_padding = padding.LiteEthMACPaddingInserter(datapath_dw, (eth_min_frame_length - eth_fcs_length))
                tx_padding = ClockDomainsRenamer(cd_tx)(tx_padding)
                self.submodules += tx_padding
                self.pipeline.append(tx_padding)

            def add_crc(self):
                tx_crc = crc.LiteEthMACCRC32Inserter(eth_phy_description(datapath_dw))
                tx_crc = BufferizeEndpoints({"sink": DIR_SINK})(tx_crc) # FIXME: Still required?
                tx_crc = ClockDomainsRenamer(cd_tx)(tx_crc)
                self.submodules += tx_crc
                self.pipeline.append(tx_crc)

            def add_preamble(self):
                tx_preamble = preamble.LiteEthMACPreambleInserter(datapath_dw)
                tx_preamble = ClockDomainsRenamer(cd_tx)(tx_preamble)
                self.submodules += tx_preamble
                self.pipeline.append(tx_preamble)

            def add_gap(self):
                tx_gap = gap.LiteEthMACGap(phy_dw)
                tx_gap = ClockDomainsRenamer("eth_tx")(tx_gap)
                self.submodules += tx_gap
                self.pipeline.append(tx_gap)

            def do_finalize(self):
                self.submodules += stream.Pipeline(*self.pipeline)

        tx_datapath = TXDatapath()
        tx_datapath.pipeline.append(self.sink)
        if not with_sys_datapath:
            # CHECKME: Verify converter/cdc order for the different cases.
            tx_datapath.add_cdc()
            if core_dw != phy_dw:
                tx_datapath.add_converter()
            if core_dw != 8:
                tx_datapath.add_last_be()
        if with_padding:
            tx_datapath.add_padding()
        if with_preamble_crc:
            tx_datapath.add_crc()
            tx_datapath.add_preamble()
        if with_sys_datapath:
            # CHECKME: Verify converter/cdc order for the different cases.
            tx_datapath.add_cdc()
            if core_dw != phy_dw:
                tx_datapath.add_converter()
            if core_dw != 8:
                tx_datapath.add_last_be()
        # Gap insertion has to occurr in phy tx domain to ensure gap is correctly maintained
        if not getattr(phy, "integrated_ifg_inserter", False):
            tx_datapath.add_gap()
        tx_datapath.pipeline.append(phy)
        self.submodules.tx_datapath = tx_datapath

        # RX Data-Path (PHY --> Core).
        # ------------------------------------------------------------------------------------------
        class RXDatapath(Module, AutoCSR):
            def __init__(self):
                self.pipeline = []
                if with_preamble_crc:
                    self.preamble_errors = CSRStatus(32)
                    self.crc_errors      = CSRStatus(32)

            def add_preamble(self):
                rx_preamble = preamble.LiteEthMACPreambleChecker(datapath_dw)
                rx_preamble = ClockDomainsRenamer(cd_rx)(rx_preamble)
                self.submodules += rx_preamble
                self.pipeline.append(rx_preamble)

                ps = PulseSynchronizer(cd_rx, "sys")
                self.submodules += ps
                self.comb += ps.i.eq(rx_preamble.error)
                self.sync += If(ps.o, self.preamble_errors.status.eq(self.preamble_errors.status + 1))

            def add_crc(self):
                rx_crc = crc.LiteEthMACCRC32Checker(eth_phy_description(datapath_dw))
                rx_crc = BufferizeEndpoints({"sink": DIR_SINK})(rx_crc) # FIXME: Still required?
                rx_crc = ClockDomainsRenamer(cd_rx)(rx_crc)
                self.submodules += rx_crc
                self.pipeline.append(rx_crc)

                ps = PulseSynchronizer(cd_rx, "sys")
                self.submodules += ps
                self.comb += ps.i.eq(rx_crc.error),
                self.sync += If(ps.o, self.crc_errors.status.eq(self.crc_errors.status + 1))

            def add_padding(self):
                rx_padding = padding.LiteEthMACPaddingChecker(datapath_dw, (eth_min_frame_length - eth_fcs_length))
                rx_padding = ClockDomainsRenamer(cd_rx)(rx_padding)
                self.submodules += rx_padding
                self.pipeline.append(rx_padding)

            def add_last_be(self):
                rx_last_be = last_be.LiteEthMACRXLastBE(phy_dw)
                rx_last_be = ClockDomainsRenamer("eth_rx")(rx_last_be)
                self.submodules += rx_last_be
                self.pipeline.append(rx_last_be)

            def add_converter(self):
                rx_converter = stream.StrideConverter(
                    description_from = eth_phy_description(phy_dw),
                    description_to   = eth_phy_description(core_dw))
                rx_converter = ClockDomainsRenamer("eth_rx")(rx_converter)
                self.submodules += rx_converter
                self.pipeline.append(rx_converter)

            def add_cdc(self):
                rx_cdc = stream.ClockDomainCrossing(eth_phy_description(core_dw),
                    cd_from = "eth_rx",
                    cd_to   = "sys",
                    depth   = rx_cdc_depth,
                    buffered = rx_cdc_buffered
                )
                self.submodules += rx_cdc
                self.pipeline.append(rx_cdc)

            def do_finalize(self):
                self.submodules += stream.Pipeline(*self.pipeline)

        rx_datapath = RXDatapath()
        rx_datapath.pipeline.append(phy)
        if with_sys_datapath:
            if core_dw != 8:
                rx_datapath.add_last_be()
            # CHECKME: Verify converter/cdc order for the different cases.
            if core_dw != phy_dw:
                rx_datapath.add_converter()
            rx_datapath.add_cdc()
        if with_preamble_crc:
            rx_datapath.add_preamble()
            rx_datapath.add_crc()
        if with_padding:
            rx_datapath.add_padding()
        if not with_sys_datapath:
            if core_dw != 8:
                rx_datapath.add_last_be()
            # CHECKME: Verify converter/cdc order for the different cases.
            if core_dw != phy_dw:
                rx_datapath.add_converter()
            rx_datapath.add_cdc()
        rx_datapath.pipeline.append(self.source)
        self.submodules.rx_datapath = rx_datapath
