#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RMII TX ------------------------------------------------------------------------------

class LiteEthPHYRMIITX(LiteXModule):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        self.converter = converter = stream.Converter(8, 2)
        self.comb += [
            converter.sink.valid.eq(sink.valid),
            converter.sink.data.eq(sink.data),
            sink.ready.eq(converter.sink.ready),
            converter.source.ready.eq(1),
        ]
        pads.tx_en.reset_less   = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(converter.source.valid),
            pads.tx_data.eq(converter.source.data)
        ]

# LiteEth PHY RMII RX ------------------------------------------------------------------------------

class LiteEthPHYRMIIRX(LiteXModule):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        converter = stream.Converter(2, 8)
        converter = ResetInserter()(converter)
        self.converter = converter

        self.delay = delay = stream.Delay(layout=[("data", 8)], n=2)
        self.comb += delay.source.connect(converter.sink)

        crs_dv   = Signal()
        crs_dv_d = Signal()
        rx_data  = Signal(2)
        self.sync += [
            crs_dv.eq(pads.crs_dv),
            crs_dv_d.eq(crs_dv),
            rx_data.eq(pads.rx_data)
        ]

        crs_first = (crs_dv & (rx_data != 0b00))
        crs_last  = (~crs_dv & ~crs_dv_d) # End of frame when 2 consecutives 0 on crs_dv.

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(crs_first,
                delay.sink.valid.eq(1),
                delay.sink.data.eq(rx_data),
                NextState("RECEIVE")
            ).Else(
               converter.reset.eq(1)
            )
        )
        fsm.act("RECEIVE",
            delay.sink.valid.eq(1),
            delay.sink.data.eq(rx_data),
            If(crs_last,
                converter.sink.last.eq(1),
                NextState("IDLE")
            )
        )
        self.comb += converter.source.connect(source)

# LiteEth PHY RMII CRG -----------------------------------------------------------------------------

class LiteEthPHYRMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, refclk_cd,
        with_hw_init_reset     = True,
        with_refclk_ddr_output = True):
        self._reset = CSRStorage()

        # # #

        # RX/TX clocks

        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()

        # When no refclk_cd, use clock_pads.ref_clk as RMII reference clock.
        if refclk_cd is None:
            self.cd_eth_rx.clk = clock_pads.ref_clk
            self.cd_eth_tx.clk = self.cd_eth_rx.clk

        # Else use refclk_cd as RMII reference clock (provided by user design).
        else:
            clk_signal = ClockSignal(refclk_cd)
            self.comb += self.cd_eth_rx.clk.eq(clk_signal)
            self.comb += self.cd_eth_tx.clk.eq(clk_signal)
            # Drive clock_pads if provided.
            if clock_pads is not None:
                if with_refclk_ddr_output:
                    self.specials += DDROutput(i1=0, i2=1, o=clock_pads.ref_clk, clk=clk_signal)
                else:
                    self.comb += clock_pads.ref_clk.eq(~clk_signal) # CHEKCME: Keep Invert?

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


# LiteEth PHY RMII ---------------------------------------------------------------------------------

class LiteEthPHYRMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 50e6
    rx_clk_freq = 50e6
    def __init__(self, clock_pads, pads, refclk_cd="eth",
        with_hw_init_reset     = True,
        with_refclk_ddr_output = True):
        self.crg = LiteEthPHYRMIICRG(clock_pads, pads, refclk_cd,
            with_hw_init_reset     = with_hw_init_reset,
            with_refclk_ddr_output = with_refclk_ddr_output,
        )
        self.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIITX(pads))
        self.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYRMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
