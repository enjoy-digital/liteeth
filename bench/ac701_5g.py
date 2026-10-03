#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""5GBASE-R Etherbone on AC701; clock and SFP setup are described in doc/a7_5g.md."""

import argparse

from migen import ClockDomain, Instance, Signal

from litex.gen import LiteXModule
from litex.build.generic_platform import IOStandard, Pins, Subsignal
from litex.build.io import DifferentialInput, DifferentialOutput
from litex.build.xilinx.vivado import vivado_build_args, vivado_build_argdict
from litex.soc.cores.bitbang import I2CMaster
from litex.soc.cores.clock import S7PLL
from litex.soc.cores.led import LedChaser
from litex.soc.integration.soc_core import SoCCore
from litex.soc.integration.builder import Builder, builder_args, builder_argdict

from litex_boards.platforms import xilinx_ac701

from liteeth.phy.serial.gtp_7series import QPLL, QPLLSettings
from liteeth.phy.serial.baser.wrappers.a7_gtp import A7_GTP_5G_BASER

# Clocking -----------------------------------------------------------------------------------------

REFCLK_FREQ = 103.125e6

# Si570 156.25 MHz -> Si5324 103.125 MHz: N31=1000, N2=4*7920, N1=4*12.
# The divider setup from the original hardware bench assumes the Si5324 power-on defaults.
SI5324_INIT = [
    (25,  0x00),
    (31,  0x00), (32, 0x00), (33, 0x0b),
    (40,  0x00), (41, 0x1e), (42, 0xef),
    (43,  0x00), (44, 0x03), (45, 0xe7),
    (46,  0x00), (47, 0x03), (48, 0xe7),
    (129, 0x00), (130, 0x00),
    (136, 0x40), # Start calibration.
]

_si5324_io = [
    ("si5324", 0,
        Subsignal("rst_n", Pins("B24"), IOStandard("LVCMOS25")),
    ),
    ("si5324_clkin", 0,
        Subsignal("p", Pins("D23"), IOStandard("LVDS_25")),
        Subsignal("n", Pins("D24"), IOStandard("LVDS_25")),
    ),
]

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # # #

        self.pll = pll = S7PLL(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst | platform.request("cpu_reset"))
        pll.register_clkin(platform.request("clk200"), 200e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin)

# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(100e6), eth_ip="192.168.1.50", sfp_host_mode="5GBASER",
        rx_polarity=0, tx_polarity=0):
        platform = xilinx_ac701.Platform()
        platform.add_extension(_si5324_io)
        SoCCore.__init__(self, platform, sys_clk_freq,
            cpu_type             = "vexriscv",
            integrated_rom_size  = 0x20000,
            integrated_sram_size = 0x4000,
            ident                = "LiteEth 5GBASE-R bench on AC701",
        )
        self.crg = _CRG(platform, sys_clk_freq)
        self.add_jtagbone()
        jtag_clk = self.jtagbone.phy.cd_jtag.clk
        platform.add_period_constraint(jtag_clk, 1e9/20e6)
        platform.add_false_path_constraints(self.crg.cd_sys.clk, jtag_clk)

        # Reference clock: Si570 -> FPGA -> Si5324 -> U3 input 1 -> MGTREFCLK0.
        clk156    = platform.request("clk156")
        clk156_se = Signal()
        si5324_in = platform.request("si5324_clkin")
        self.specials += [
            DifferentialInput(clk156.p, clk156.n, clk156_se),
            DifferentialOutput(clk156_se, si5324_in.p, si5324_in.n),
        ]
        platform.add_period_constraint(clk156_se, 1e9/156.25e6)
        self.comb += [
            platform.request("si5324").rst_n.eq(1),
            platform.request("i2c_mux_reset").eq(1),
            platform.request("sfp_mgt_clk_sel0").eq(1),
            platform.request("sfp_mgt_clk_sel1").eq(0),
            # R18 is TX_DISABLE (active high), despite the platform's resource name.
            platform.request("sfp_tx_disable_n").eq(0),
        ]
        refclk_pads = platform.request("gtp_refclk", 0)
        refclk      = Signal()
        self.specials += Instance("IBUFDS_GTE2",
            i_CEB = 0,
            i_I   = refclk_pads.p,
            i_IB  = refclk_pads.n,
            o_O   = refclk,
        )
        platform.add_period_constraint(refclk, 1e9/REFCLK_FREQ)
        self.qpll = QPLL(gtrefclk0=refclk, qpllsettings0=QPLLSettings(
            refclksel  = 0b001,
            fbdiv      = 5,
            fbdiv_45   = 5,
            refclk_div = 1,
        ))

        # PHY / Etherbone. MACCore supplies complete-frame TX and drop-on-overflow RX queues.
        self.ethphy = A7_GTP_5G_BASER(self.qpll.channels[0], platform.request("sfp", 0),
            sys_clk_freq = sys_clk_freq,
            refclk_freq  = REFCLK_FREQ,
            rx_polarity  = rx_polarity,
            tx_polarity  = tx_polarity,
        )
        self.ethphy.add_timing_constraints(platform)
        self.add_etherbone(phy=self.ethphy, ip_address=eth_ip, data_width=32, buffer_depth=256,
            with_timing_constraints=False)
        platform.add_false_path_constraints(
            self.crg.cd_sys.clk, self.ethphy.txoutclk, self.ethphy.rxoutclk)

        # BIOS programs the clock on mux channel 7, then configures the SFP on channel 4.
        self.sfp_i2c = I2CMaster(platform.request("i2c"))
        # PCA9548 has no register address; both bytes select the same channel.
        self.sfp_i2c.add_init(addr=0x74, init=[(0x80, 0x80)])
        self.sfp_i2c.add_init(addr=0x68, init=SI5324_INIT)
        self.add_config("SFP_0_I2C",         "sfp_i2c")
        self.add_config("SFP_0_HOST_MODE",   sfp_host_mode)
        self.add_config("SFP_0_MUX_ADDR",    0x74)
        self.add_config("SFP_0_MUX_CHANNEL", 4)
        self.leds = LedChaser(platform.request_all("user_led"), sys_clk_freq)

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Build/load.
    parser.add_argument("--build",         action="store_true", help="Build bitstream.")
    parser.add_argument("--load",          action="store_true", help="Load bitstream.")

    # Ethernet.
    parser.add_argument("--sys-clk-freq",  type=float, default=100e6)
    parser.add_argument("--eth-ip",        default="192.168.1.50")
    parser.add_argument("--sfp-host-mode", choices=["5GBASER", "AUTO"], default="5GBASER")
    parser.add_argument("--rx-polarity",   type=int, choices=[0, 1], default=0)
    parser.add_argument("--tx-polarity",   type=int, choices=[0, 1], default=0)

    builder_args(parser)
    vivado_build_args(parser)
    # The gearbox enable drives the PCS throughout the TX domain.
    parser.set_defaults(
        vivado_place_directive               = "Explore",
        vivado_post_place_phys_opt_directive = "AggressiveFanoutOpt",
        vivado_route_directive               = "Explore",
        vivado_post_route_phys_opt_directive = "AggressiveExplore",
    )
    args = parser.parse_args()

    soc = BenchSoC(
        sys_clk_freq  = int(args.sys_clk_freq),
        eth_ip        = args.eth_ip,
        sfp_host_mode = args.sfp_host_mode,
        rx_polarity   = args.rx_polarity,
        tx_polarity   = args.tx_polarity,
    )
    builder = Builder(soc, **builder_argdict(args))
    builder.build(run=args.build, **vivado_build_argdict(args))
    if args.load:
        soc.platform.create_programmer().load_bitstream(builder.get_bitstream_filename(mode="sram"))


if __name__ == "__main__":
    main()
