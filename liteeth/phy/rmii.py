#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.io import DDROutput

from liteeth.common import *
from liteeth.phy.common import *


def converter_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)


class LiteEthPHYRMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        self.submodules.converter = converter = stream.StrideConverter(
            description_from = converter_description(8),
            description_to   = converter_description(2),
        )
        self.comb += [
            converter.sink.valid.eq(sink.valid),
            converter.sink.data.eq(sink.data),
            sink.ready.eq(converter.sink.ready),
            converter.source.ready.eq(1)
        ]
        pads.tx_en.reset_less   = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(converter.source.valid),
            pads.tx_data.eq(converter.source.data)
        ]


class LiteEthPHYRMIIRX(Module):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        converter = stream.StrideConverter(
            description_from = converter_description(2),
            description_to   = converter_description(8),
        )
        converter = ResetInserter()(converter)
        self.submodules.converter = converter

        converter_sink_valid = Signal()
        converter_sink_data  = Signal(2)

        self.specials += [
            MultiReg(converter_sink_valid, converter.sink.valid, n=2),
            MultiReg(converter_sink_data,  converter.sink.data,  n=2)
        ]

        crs_dv   = Signal()
        crs_dv_d = Signal()
        rx_data  = Signal(2)
        self.sync += [
            crs_dv.eq(pads.crs_dv),
            crs_dv_d.eq(crs_dv),
            rx_data.eq(pads.rx_data)
        ]

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(crs_dv & (rx_data != 0b00),
                converter_sink_valid.eq(1),
                converter_sink_data.eq(rx_data),
                NextState("RECEIVE")
            ).Else(
               converter.reset.eq(1)
            )
        )
        fsm.act("RECEIVE",
            converter_sink_valid.eq(1),
            converter_sink_data.eq(rx_data),
            # End of frame when 2 consecutives 0 on crs_dv.
            If(~(crs_dv | crs_dv_d),
              converter.sink.last.eq(1),
              NextState("IDLE")
            )
        )
        self.comb += converter.source.connect(source)


class LiteEthPHYRMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, refclk_cd,
        with_hw_init_reset     = True,
        with_refclk_ddr_output = True):
        self._reset = CSRStorage()

        # # #

        # RX/TX clocks

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        # When no refclk_cd, use clock_pads.ref_clk as RMII reference clock.
        if refclk_cd is None:
            self.comb += self.cd_eth_rx.clk.eq(clock_pads.ref_clk)
            self.comb += self.cd_eth_tx.clk.eq(clock_pads.ref_clk)

        # Else use refclk_cd as RMII reference clock (provided by user design).
        else:
            self.comb += self.cd_eth_rx.clk.eq(ClockSignal(refclk_cd))
            self.comb += self.cd_eth_tx.clk.eq(ClockSignal(refclk_cd))
            # Drive clock_pads if provided.
            if clock_pads is not None:
                if with_refclk_ddr_output:
                    self.specials += DDROutput(i1=0, i2=1, o=clock_pads.ref_clk, clk=ClockSignal("eth_tx"))
                else:
                    self.comb += clock_pads.ref_clk.eq(~ClockSignal("eth_tx")) # CHEKCME: Keep Invert?

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRMII(Module, AutoCSR):
    dw          = 8
    tx_clk_freq = 50e6
    rx_clk_freq = 50e6
    def __init__(self, clock_pads, pads, refclk_cd="eth",
        with_hw_init_reset     = True,
        with_refclk_ddr_output = True):
        self.submodules.crg = LiteEthPHYRMIICRG(clock_pads, pads, refclk_cd,
            with_hw_init_reset     = with_hw_init_reset,
            with_refclk_ddr_output = with_refclk_ddr_output,
        )
        self.submodules.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIITX(pads))
        self.submodules.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
