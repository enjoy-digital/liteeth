#!/usr/bin/env python3

#
# Derived from litex_boards/targets/alibaba_xcku3p.py, which is part of LiteX-Boards.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com
# SPDX-License-Identifier: BSD-2-Clause

import argparse
import sys

from migen import *

from litex.gen import *

from litex_boards.platforms import alibaba_xcku3p

from litex.soc.integration.soc import *
from litex.soc.integration.builder  import *

from litex.soc.interconnect import stream

from litex.soc.cores.clock     import *
from litex.soc.cores.led       import LedChaser

from liteeth.phy.usp_gty_1000basex import USP_GTY_1000BASEX
from liteeth.phy.us_gt_10g_baser import USP_GTY_10G_BASER

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()
        self.cd_eth = ClockDomain()

        # Clk.
        clk100 = platform.request("clk100")

        # PLL.
        self.pll = pll = USPMMCM(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)
        pll.create_clkout(self.cd_eth, 200e6)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin) # Ignore sys_clk to pll.clkin path created by SoC's rst.

# BenchSoC ------------------------------------------------------------------------------------------

class BenchSoC(SoCMini):
    def __init__(self, sys_clk_freq=200e6, eth_speed="10g", eth_sfp=0, eth_ip="192.168.1.50"):
        platform = alibaba_xcku3p.Platform()

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(
            self, platform, clk_freq=sys_clk_freq,
            ident="LiteEth 10G demo on Alibaba Cloud KU3P Board",
            ident_version=True,
        )

        self.add_jtagbone()

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # Etherbone --------------------------------------------------------------------------------
        eth_refclk = self.platform.request("sfp_mgt_clk", 0)
        eth_refclk_freq = 156.25e6
        platform.add_period_constraint(eth_refclk.p, 1e9/eth_refclk_freq)

        if eth_speed == "1g":
            self.ethphy = USP_GTY_1000BASEX(
                eth_refclk,
                data_pads=self.platform.request("sfp", eth_sfp),
                sys_clk_freq=self.clk_freq,
                refclk_freq=eth_refclk_freq,
                refclk_from_fabric =False,
            )
        else:
            self.ethphy = USP_GTY_10G_BASER(
                refclk_or_clk_pads=eth_refclk,
                data_pads=self.platform.request("sfp", eth_sfp),
                sys_clk_freq=self.clk_freq,
                refclk_freq=eth_refclk_freq,
            )

        self.add_etherbone(
            phy=self.ethphy,
            ip_address=eth_ip,
            data_width=64,
            buffer_depth=255,
        )

        self.add_ram("sram", origin=0x20000000, size=0x1000)

        # LEDs -------------------------------------------------------------------------------------
        self.leds = LedChaser(
            pads         = platform.request_all("user_led"),
            sys_clk_freq = sys_clk_freq)

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Alibaba Cloud KU3P board, with 1G or 10G Ethernet.")
    # Note that the system clock frequency must be comfortably above 156.25e6 in order to handle 10G traffic.
    parser.add_argument("--sys-clk-freq", default=200e6, type=float,            help="System clock frequency.")
    parser.add_argument("--eth-speed",    default="10g", choices=["1g", "10g"], help="Ethernet speed: 1000BASE-X or 10GBASE-R.")
    parser.add_argument("--eth-sfp",      default=0, type=int, choices=[0, 1],  help="Ethernet SFP.")
    parser.add_argument("--eth-ip",       default="192.168.1.50",               help="Etherbone IP address.")
    parser.add_argument("--build",        action="store_true", help="Build bitstream")
    parser.add_argument("--load",         action="store_true", help="Load bitstream")
    args = parser.parse_args()

    soc = BenchSoC(
        sys_clk_freq   = args.sys_clk_freq,
        eth_speed      = args.eth_speed,
        eth_sfp        = args.eth_sfp,
        eth_ip         = args.eth_ip,
    )

    builder = Builder(soc, csr_csv="csr.csv")
    if args.build:
        builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()
