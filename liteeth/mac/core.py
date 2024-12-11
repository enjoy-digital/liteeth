#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V. <goemansrowan@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from liteeth.common import *
from liteeth.mac    import gap, preamble, crc, padding, last_be
from liteeth.mac.common import *

from migen.genlib.cdc import PulseSynchronizer

from litex.soc.interconnect.stream import BufferizeEndpoints, DIR_SOURCE, DIR_SINK

# MAC Core -----------------------------------------------------------------------------------------

class LiteEthMACCore(LiteXModule):
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

        if with_sys_datapath:
            cd_tx       = "sys"
            cd_rx       = "sys"
            datapath_dw = core_dw
        else:
            cd_tx       = "eth_tx"
            cd_rx       = "eth_rx"
            datapath_dw = phy_dw

        # If the PHY specifies preamble, CRC, or padding behavior, use it.
        if hasattr(phy, "with_preamble_crc"):
            with_preamble_crc = phy.with_preamble_crc
        if hasattr(phy, "with_padding"):
            with_padding = phy.with_padding

        # CSRs.
        if with_preamble_crc:
            self.preamble_crc = CSRStatus(reset=1)

        # TX Data-Path (Core --> PHY).
        # ------------------------------------------------------------------------------------------
        class TXDatapath(LiteXModule):
            def __init__(self):
                self.pipeline = []

            def add_cdc(self, dw):
                tx_cdc = stream.ClockDomainCrossing(eth_phy_description(dw),
                    cd_from  = "sys",
                    cd_to    = "eth_tx",
                    depth    = tx_cdc_depth,
                    buffered = tx_cdc_buffered,
                )
                self.submodules += tx_cdc
                self.pipeline.append(tx_cdc)

            def add_converter(self, cd):
                tx_converter = stream.StrideConverter(
                    description_from = eth_phy_description(core_dw),
                    description_to   = eth_phy_description(phy_dw))
                tx_converter = ClockDomainsRenamer(cd)(tx_converter)
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

            def add_domain_switch(self):
                dw = core_dw
                if core_dw < phy_dw:
                    dw = phy_dw
                    self.add_converter("sys")
                self.add_cdc(dw)
                if core_dw > phy_dw:
                    self.add_converter("eth_tx")
                    self.add_last_be()

            def do_finalize(self):
                self.submodules += stream.Pipeline(*self.pipeline)

        self.tx_datapath = tx_datapath = TXDatapath()
        tx_datapath.pipeline.append(self.sink)
        if not with_sys_datapath:
            tx_datapath.add_domain_switch()
        if with_padding:
            tx_datapath.add_padding()
        if with_preamble_crc:
            tx_datapath.add_crc()
            tx_datapath.add_preamble()
        if with_sys_datapath:
            tx_datapath.add_domain_switch()
        # Gap insertion has to occurr in phy tx domain to ensure gap is correctly maintained.
        if not getattr(phy, "integrated_ifg_inserter", False):
            tx_datapath.add_gap()
        tx_datapath.pipeline.append(phy)

        # RX Data-Path (PHY --> Core).
        # ------------------------------------------------------------------------------------------
        class RXDatapath(LiteXModule):
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

            def add_converter(self, cd):
                rx_converter = stream.StrideConverter(
                    description_from = eth_phy_description(phy_dw),
                    description_to   = eth_phy_description(core_dw))
                rx_converter = ClockDomainsRenamer(cd)(rx_converter)
                self.submodules += rx_converter
                self.pipeline.append(rx_converter)

            def add_cdc(self, dw):
                rx_cdc = stream.ClockDomainCrossing(eth_phy_description(dw),
                    cd_from  = "eth_rx",
                    cd_to    = "sys",
                    depth    = rx_cdc_depth,
                    buffered = rx_cdc_buffered,
                )
                self.submodules += rx_cdc
                self.pipeline.append(rx_cdc)

            def add_domain_switch(self):
                dw = phy_dw
                if phy_dw < core_dw:
                    dw = core_dw
                    self.add_last_be()
                    self.add_converter("eth_rx")
                self.add_cdc(dw)
                if phy_dw > core_dw:
                    self.add_converter("sys")
                    last_handler = LiteEthLastHandler(eth_phy_description(core_dw))
                    self.submodules += last_handler
                    self.pipeline.append(last_handler)

            def do_finalize(self):
                self.submodules += stream.Pipeline(*self.pipeline)

        self.rx_datapath = rx_datapath = RXDatapath()
        rx_datapath.pipeline.append(phy)
        if with_sys_datapath:
            rx_datapath.add_domain_switch()
        if with_preamble_crc:
            rx_datapath.add_preamble()
            rx_datapath.add_crc()
        if with_padding:
            rx_datapath.add_padding()
        if not with_sys_datapath:
            rx_datapath.add_domain_switch()
        rx_datapath.pipeline.append(self.source)
