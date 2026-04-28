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

from liteeth.common     import *
from liteeth.phy.common import *

from litex.soc.cores.clock import Agilex3PLL, Agilex5PLL

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

def io_ibuf(i, o):
    return [
        Instance("tennm_ph2_io_ibuf",
            p_bus_hold        = "BUS_HOLD_OFF",
            p_buffer_usage    = "REGULAR",
            p_equalization    = "EQUALIZATION_OFF",
            p_io_standard     = "IO_STANDARD_IOSTD_OFF",
            p_rzq_id          = "RZQ_ID_RZQ0",
            p_schmitt_trigger = "SCHMITT_TRIGGER_OFF",
            p_termination     = "TERMINATION_RT_OFF",
            p_toggle_speed    = "TOGGLE_SPEED_SLOW",
            p_usage_mode      = "USAGE_MODE_GPIO",
            p_vref            = "VREF_OFF",
            p_weak_pull_down  = "WEAK_PULL_DOWN_OFF",
            p_weak_pull_up    = "WEAK_PULL_UP_OFF",
            i_i               = i,
            o_o               = o,
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
            data_d = Signal()
            self.sync += data_d.eq(sink.data[i + 4])
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

        self.rx_ctl = CSRStatus() # Unused but required to avoid fitter fails with unused tennm_ph2_ddio_in outputs

        # # #

        rx_ctl_ibuf  = Signal()
        rx_ctl       = Signal(2)

        rx_data_ibuf = Signal(4)
        rx_data      = Signal(8)

        self.specials += [
            io_ibuf(pads.rx_ctl, rx_ctl_ibuf),
            DDRInput(
                clk = ClockSignal("eth_rx"),
                i   = rx_ctl_ibuf,
                o1  = rx_ctl,
                o2  = self.rx_ctl.status,
            ),
        ]

        for i in range(4):
            self.specials += [
                io_ibuf(pads.rx_data[i], rx_data_ibuf[i]),
                DDRInput(
                    clk = ClockSignal("eth_rx"),
                    i   = rx_data_ibuf[i],
                    o1  = rx_data[i],
                    o2  = rx_data[i+4],
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
        tx_delay           = 2e-9,
        hw_reset_cycles    = 256
        ):
        self._reset = CSRStorage()

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
        tx_pll.register_clkin(ref_tx_clk,            125e6, "tx_pll_in")
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
            self.comb += pads.rst_n.eq(~reset)
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
        tx_delay           = 2e-9,
        rx_delay           = 2e-9,
        hw_reset_cycles    = 256
        ):
        self.crg      = LiteEthPHYRGMIICRG(platform, clock_pads, pads, ref_tx_clk,
            with_hw_init_reset = with_hw_init_reset,
            tx_delay           = tx_delay,
            hw_reset_cycles    = hw_reset_cycles,
        )
        self.tx       = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.rx       = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
