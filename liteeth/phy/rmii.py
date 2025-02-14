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

# LiteEth PHY RMII Timer ---------------------------------------------------------------------------

class LiteEthPHYRMIITimer(LiteXModule):
    def __init__(self, speed):
        self.rst = Signal() # i.
        self.ce  = Signal() # o.

        # # #

        timer = Signal(4)
        self.comb += self.ce.eq(timer == 0)
        self.sync += [
            # Decrement timer.
            timer.eq(timer - 1),
            # Reload Timer.
            If(self.ce | self.rst,
                Case(speed, {
                    0b0: timer.eq(9), #  10Mbps.
                    0b1: timer.eq(0), # 100Mbps.
                })
            )
        ]

# LiteEth PHY RMII Speed Detect --------------------------------------------------------------------

class LiteEthPHYRMIISpeedDetect(LiteXModule):
    def __init__(self, crs_dv, rx_data, crs_last):
        self.speed  = Signal() # 0: 10Mbps, 1: 100Mbps.

        # # #

        # Signals.
        count = Signal(10)

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(crs_dv,
                NextValue(count, 0),
                NextState("DETECT")
            )
        )

        fsm.act("DETECT",
            NextValue(count, count + 1),
            If(rx_data[0],
                If(count < 20,
                    NextValue(self.speed, 1), # 100Mbps
                ).Else(
                    NextValue(self.speed, 0), # 10Mbps
                ),
                NextState("HOLD_SPEED"),
            ),
            If(~crs_dv,
                # If packet ends too soon, hold speed.
                NextState("HOLD_SPEED")
            )
        )

        fsm.act("HOLD_SPEED",
            If(crs_last,
                NextState("IDLE")
            )
        )

# LiteEth PHY RMII TX ------------------------------------------------------------------------------

class LiteEthPHYRMIITX(LiteXModule):
    def __init__(self, pads, clk_signal):
        self.sink  = sink = stream.Endpoint(eth_phy_description(8))
        self.speed = Signal() # 0: 10Mbps / 1: 100Mbps.

        # # #

        # Speed Timer for 10Mbps/100Mbps.
        # -------------------------------
        self.timer = timer = LiteEthPHYRMIITimer(speed=self.speed)
        self.comb += timer.rst.eq(~sink.valid)

        # Converter: 8-bit to 2-bit.
        # --------------------------
        self.converter = converter = stream.Converter(8, 2)

        # Datapath: Sink -> Converter.
        # ----------------------------
        self.comb += [
            sink.connect(converter.sink, keep={"valid", "ready", "data"}),
            converter.source.ready.eq(timer.ce),
        ]

        # Output (Sync).
        # --------------
        self.specials += SDROutput(i=converter.source.valid, o=pads.tx_en,   clk=clk_signal)
        self.specials += SDROutput(i=converter.source.data,  o=pads.tx_data, clk=clk_signal)


# LiteEth PHY RMII RX ------------------------------------------------------------------------------

class LiteEthPHYRMIIRX(LiteXModule):
    def __init__(self, pads, clk_signal, speed_counter_threshold=20):
        self.source = source = stream.Endpoint(eth_phy_description(8))
        self.speed = Signal() # 0: 10Mbps / 1: 100Mbps.

        # # #

        # Input (Sync).
        # -------------
        crs_dv_i  = Signal()
        rx_data_i = Signal(2)
        self.specials += SDRInput(i=pads.crs_dv,  o=crs_dv_i,  clk=clk_signal)
        self.specials += SDRInput(i=pads.rx_data, o=rx_data_i, clk=clk_signal)

        # Speed Timer for 10Mbps/100Mbps.
        # -------------------------------
        self.timer = timer = LiteEthPHYRMIITimer(speed=self.speed)

        # Latch Input.
        # ------------
        crs_dv  = Signal()
        rx_data = Signal(2)
        self.sync += If(timer.ce,
            crs_dv.eq(crs_dv_i),
            rx_data.eq(rx_data_i),
        )

        # Converter: 2-bit to 8-bit.
        # --------------------------
        self.converter = converter = stream.Converter(2, 8)

        # Delay.
        # ------
        # Add a delay to align the data with the frame boundaries since the end-of-frame condition
        # (2 consecutive `crs_dv` signals low) is detected with a few cycles delay.
        self.delay = delay = stream.Delay(layout=[("data", 2)], n=2)

        # Frame Delimitation.
        # -------------------
        crs_first = Signal()
        crs_last  = Signal()
        crs_run   = Signal()
        crs_dv_d  = Signal()
        self.comb += If(timer.ce,
            crs_first.eq(crs_dv & (rx_data != 0b00)), # Start of frame on crs_dv high and non-null data.
            crs_last.eq(~crs_dv & ~crs_dv_d),         # End of frame on 2 consecutive crs_dv low.
        )
        self.sync += [
            If(timer.ce,  crs_dv_d.eq(crs_dv)),
            If(crs_first, crs_run.eq(1)),
            If(crs_last,  crs_run.eq(0)),
        ]

        # Datapath: Input -> Delay -> Converter -> Source.
        # ------------------------------------------------
        self.comb += [
            delay.sink.valid.eq(crs_first | (crs_run & timer.ce)),
            delay.sink.data.eq(rx_data),
            delay.source.ready.eq(~crs_run), # Flush pipeline when in idle.
            delay.source.connect(converter.sink, keep={"data"}),
            If(crs_run & timer.ce,
                delay.source.connect(converter.sink, keep={"valid", "ready"}),
                converter.sink.last.eq(crs_last),
            ),
            converter.source.connect(source),
        ]

        # Speed Detection.
        # ----------------
        self.speed_detect = LiteEthPHYRMIISpeedDetect(
            crs_dv   = crs_dv_i,
            rx_data  = rx_data_i,
            crs_last = crs_last,
        )
        self.comb += self.speed.eq(self.speed_detect.speed)

# LiteEth PHY RMII CRG -----------------------------------------------------------------------------

class LiteEthPHYRMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, refclk_cd,
        with_hw_init_reset     = True,
        with_refclk_ddr_output = True):
        self._reset = CSRStorage()

        # # #

        # RX/TX clocks.
        # -------------
        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()

        # When no refclk_cd, use clock_pads.ref_clk as RMII reference clock.
        if refclk_cd is None:
            self.cd_eth_rx.clk = clock_pads.ref_clk
            self.cd_eth_tx.clk = self.cd_eth_rx.clk
            self.clk_signal    = self.cd_eth_rx.clk

        # Else use refclk_cd as RMII reference clock (provided by user design).
        else:
            self.clk_signal = clk_signal = ClockSignal(refclk_cd)
            self.comb += self.cd_eth_rx.clk.eq(clk_signal)
            self.comb += self.cd_eth_tx.clk.eq(clk_signal)
            # Drive clock_pads if provided.
            if clock_pads is not None:
                if with_refclk_ddr_output:
                    self.specials += DDROutput(i1=0, i2=1, o=clock_pads.ref_clk, clk=clk_signal)
                else:
                    self.comb += clock_pads.ref_clk.eq(~clk_signal) # CHEKCME: Keep Invert?

        # Reset.
        # ------
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

        # CRG.
        # ----
        self.crg = LiteEthPHYRMIICRG(clock_pads, pads, refclk_cd,
            with_hw_init_reset     = with_hw_init_reset,
            with_refclk_ddr_output = with_refclk_ddr_output,
        )

        # TX/RX.
        # ------
        self.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIITX(pads, self.crg.clk_signal))
        self.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYRMIIRX(pads, self.crg.clk_signal))
        self.comb             += self.tx.speed.eq(self.rx.speed)
        self.sink, self.source = self.tx.sink, self.rx.source

        # MDIO.
        # -----
        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
