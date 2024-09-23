#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import SDRInput, SDROutput, DDROutput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RMII TX ------------------------------------------------------------------------------

class LiteEthPHYRMIITX(LiteXModule):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        # Converter: 8-bit to 2-bit.
        # --------------------------
        self.converter = converter = stream.Converter(8, 2)

        # Datapath: Sink -> Converter.
        # ----------------------------
        self.comb += [
            sink.connect(converter.sink, keep={"valid", "ready", "data"}),
            converter.source.ready.eq(1),
        ]

        # Output (Sync).
        # --------------
        self.specials += SDROutput(i=converter.source.valid, o=pads.tx_en)
        for i in range(2):
            self.specials += SDROutput(i=converter.source.data[i], o=pads.tx_data[i])


# LiteEth PHY RMII RX ------------------------------------------------------------------------------

class LiteEthPHYRMIIRX(LiteXModule):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        # Input (Sync).
        # -------------
        crs_dv   = Signal()
        rx_data  = Signal(2)
        self.specials += SDRInput(i=pads.crs_dv,  o=crs_dv)
        for i in range(2):
            self.specials += SDRInput(i=pads.rx_data[i], o=rx_data[i])

        # Converter: 2-bit to 8-bit.
        # --------------------------
        self.converter = converter = stream.Converter(2, 8)

        # Delay.
        # ------
        # Add a delay to align the data with the frame boundaries since the end-of-frame condition
        # (2 consecutive `crs_dv` signals low) is detected with a few cycles delay.
        self.delay = delay = stream.Delay(layout=[("data", 8)], n=2)

        # Frame Delimitation.
        # -------------------
        crs_dv_d  = Signal()
        crs_first = Signal()
        crs_last  = Signal()
        crs_run   = Signal()
        self.sync += crs_dv_d.eq(crs_dv)
        self.comb += [
            crs_first.eq(crs_dv & (rx_data != 0b00)), # Start of frame on crs_dv high and non-null data.
            crs_last.eq(~crs_dv & ~crs_dv_d),         # End of frame on 2 consecutive crs_dv low.
        ]
        self.sync += [
            If(crs_first, crs_run.eq(1)),
            If(crs_last,  crs_run.eq(0)),
        ]

        # Datapath: Input -> Delay -> Converter -> Source.
        # ------------------------------------------------
        self.comb += [
            delay.source.ready.eq(1), # Ready by default to flush pipeline.
            delay.sink.valid.eq(crs_first | crs_run),
            delay.sink.data.eq(rx_data),
            If(crs_run,
                converter.sink.last.eq(crs_last),
                delay.source.connect(converter.sink, keep={"valid", "ready", "data"})
            ),
            converter.source.connect(source),
        ]


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
