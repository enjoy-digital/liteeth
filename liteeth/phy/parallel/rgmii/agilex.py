#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Gwenhael Goavec-Merou <gwenhael.goavec-merou@trabucayre.com>
#
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import ClkInput, ClkOutput, DDROutput, DDRInput

from litex.soc.cores.clock import Agilex3PLL, Agilex5PLL

from liteeth.common     import *
from liteeth.phy.common import *

# Utils --------------------------------------------------------------------------------------------

def io_obuf(i, o):
    return [
        Instance("tennm_ph2_io_obuf",
            p_buffer_usage            = "REGULAR",
            p_dynamic_pull_up_enabled = "FALSE",
            p_equalization            = "EQUALIZATION_OFF",
            p_io_standard             = "IO_STANDARD_IOSTD_OFF",
            p_open_drain              = "OPEN_DRAIN_OFF",
            p_rzq_id                  = "RZQ_ID_RZQ0",
            p_slew_rate               = "SLEW_RATE_SLOW",
            p_termination             = "TERMINATION_SERIES_OFF",
            p_toggle_speed            = "TOGGLE_SPEED_SLOW",
            p_usage_mode              = "USAGE_MODE_GPIO",
            i_i                       = i,
            o_o                       = o,
        )
    ]

def ddio_in(clk, i, o1, o2):
    return [
        Instance("tennm_ph2_ddio_in",
            p_mode      = "MODE_DDR_W_DLY",
            p_sclr_ena  = "SCLR_ENA_NONE",
            p_asclr_ena = "ASCLR_ENA_NONE",
            i_ena       = Constant(1, 1),
            i_areset    = Constant(1, 1),
            i_sreset    = Constant(0, 1),
            i_datain    = i,
            o_regoutlo  = o1,
            o_regouthi  = o2,
            i_clk       = clk,
        )
    ]

def pipe_in(clk, i, o):
    return [
        Instance("tennm_p2c_pipe_reg",
            i_d   = i,
            i_clk = clk,
            o_q   = o,
        )
    ]

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_obuf  = Signal()
        tx_data_obuf = Signal(4)

        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = sink.valid,
                i2  = sink.valid,
                o   = tx_ctl_obuf,
            ),
            io_obuf(tx_ctl_obuf, pads.tx_ctl),
        ]

        for i in range(4):
            self.specials += [
                DDROutput(
                    clk = ClockSignal("eth_tx"),
                    i1  = sink.data[i],
                    i2  = sink.data[i+4],
                    o   = tx_data_obuf[i],
                ),
                io_obuf(tx_data_obuf[i], pads.tx_data[i]),
            ]

        self.sync += sink.ready.eq(1)

# LiteEthPHYRGMII RX -----------------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, pads):
        self.source    = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_ctl_pipe = Signal(2)
        rx_ctl_raw  = Signal(2)
        rx_ctl      = Signal()

        rx_data_raw = Signal(8)
        rx_data     = Signal(8)

        self.specials += [
            ddio_in(ClockSignal("eth_rx"), pads.rx_ctl,    rx_ctl_pipe[0], rx_ctl_pipe[1]),
            pipe_in(ClockSignal("eth_rx"), rx_ctl_pipe[0], rx_ctl_raw[0]),
            pipe_in(ClockSignal("eth_rx"), rx_ctl_pipe[1], rx_ctl_raw[1]),
        ]

        for i in range(4):
            rx_data_pipe = Signal(2)
            self.specials += [
                ddio_in(ClockSignal("eth_rx"), pads.rx_data[i], rx_data_pipe[0], rx_data_pipe[1]),
                pipe_in(ClockSignal("eth_rx"), rx_data_pipe[0], rx_data_raw[i]),
                pipe_in(ClockSignal("eth_rx"), rx_data_pipe[1], rx_data_raw[i+4]),
            ]

        rx_ctl_raw_d  = Signal(2)
        rx_data_raw_d = Signal(8)
        rx_align      = Signal()
        rx_active     = Signal()
        self.sync += [
            rx_ctl_raw_d.eq(rx_ctl_raw),
            rx_data_raw_d.eq(rx_data_raw)
        ]

        start_aligned   = Signal()
        start_unaligned = Signal()
        self.comb += [
            start_aligned.eq(~rx_active &  rx_ctl_raw[0] &  rx_ctl_raw[1]),
            start_unaligned.eq(~rx_active &  rx_ctl_raw[0] & ~rx_ctl_raw[1]),
        ]

        self.comb += [
            rx_data.eq(rx_data_raw),
            rx_ctl.eq(rx_ctl_raw[0]),
            If(rx_align,
                rx_data.eq(Cat(rx_data_raw_d[4:8], rx_data_raw[0:4])),
                rx_ctl.eq(rx_ctl_raw_d[1]),
            ),
            If(start_aligned,
                rx_data.eq(rx_data_raw),
                rx_ctl.eq(rx_ctl_raw[0]),
            ),
            If(start_unaligned,
                rx_ctl.eq(0),
            ),
        ]

        self.sync += [
            rx_active.eq(rx_ctl),
            If(~rx_active,
                If(start_aligned,
                    rx_align.eq(0),
                ).Elif(start_unaligned,
                    rx_align.eq(1),
                )
            ),
        ]

        rx_ctl_d = Signal()
        self.sync += rx_ctl_d.eq(rx_ctl)

        last = Signal()
        self.comb += last.eq(~rx_ctl & rx_ctl_d)
        self.sync += [
            source.valid.eq(rx_ctl),
            source.data.eq(rx_data)
        ]
        self.comb += source.last.eq(last)

# LiteEthPHYRGMII CRG ----------------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, platform, clock_pads, pads, ref_tx_clk,
        with_hw_init_reset = True,
        with_phy_reset     = True,
        tx_delay           = 2e-9,
        hw_reset_cycles    = 256
        ):
        self._reset = CSRStorage(description="PHY reset.")

        # # #

        # RX clock.
        self.cd_eth_rx     = ClockDomain()
        self.cd_eth_rx.clk = clock_pads.rx

        # TX clock.

        self.cd_eth_tx         = ClockDomain()
        self.cd_eth_tx_delayed = ClockDomain(reset_less=True)
        tx_phase               = 125e6*tx_delay*360
        assert tx_phase < 360
        assert tx_phase == 90

        pll_cls = {
            "A3": Agilex3PLL,
            "A5": Agilex5PLL,
        }.get(platform.device[:2], None)

        assert pll_cls is not None, f"Unknown Agilex variant with model {platform.device}"

        speedgrade = platform.device[-2:]

        self.tx_pll = tx_pll = pll_cls(platform, speedgrade=f"-{speedgrade}")
        self.comb += tx_pll.reset.eq(ResetSignal("sys"))
        tx_pll.register_clkin(ref_tx_clk,            125e6)
        tx_pll.create_clkout(self.cd_eth_tx,         125e6, with_reset=False)
        tx_pll.create_clkout(self.cd_eth_tx_delayed, 125e6, phase=tx_phase)

        eth_tx_clk_obuf = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx_delayed"),
                i1  = 1,
                i2  = 0,
                o   = eth_tx_clk_obuf,
            ),
            io_obuf(eth_tx_clk_obuf, clock_pads.tx),
        ]

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset(cycles=hw_reset_cycles)
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            if with_phy_reset:
                self.comb += pads.rst_n.eq(~reset)
            else:
                self.comb += pads.rst_n.eq(1)

        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]

class LiteEthPHYRGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6

    def __init__(self, platform, clock_pads, pads, ref_tx_clk,
        with_hw_init_reset = True,
        with_phy_reset     = True,
        tx_delay           = 2e-9,
        rx_delay           = 2e-9,
        hw_reset_cycles    = 256
        ):
        self.crg      = LiteEthPHYRGMIICRG(platform, clock_pads, pads, ref_tx_clk,
            with_hw_init_reset = with_hw_init_reset,
            with_phy_reset     = with_phy_reset,
            tx_delay           = tx_delay,
            hw_reset_cycles    = hw_reset_cycles,
        )
        self.tx       = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.rx       = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
