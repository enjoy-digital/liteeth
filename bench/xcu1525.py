#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2021-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import argparse

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.generic_platform import *
from litex_boards.platforms import sqrl_xcu1525

from litex.soc.cores.clock import *
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.phy.usp_gty_1000basex import USP_GTY_1000BASEX

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.cd_sys = ClockDomain()
        self.cd_eth = ClockDomain()

        # # #

        # Main PLL.
        self.main_pll = main_pll = USPMMCM(speedgrade=-2)
        main_pll.register_clkin(platform.request("clk300"), 300e6)
        main_pll.create_clkout(self.cd_sys, sys_clk_freq)
        main_pll.create_clkout(self.cd_eth, 200e6, margin=0)

# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(125e6)):
        platform = sqrl_xcu1525.Platform()

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident          = "LiteEth bench on XCU1525",
            ident_version  = True
        )

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # Etherbone --------------------------------------------------------------------------------

        platform.add_extension([
            # SFP.
            ("qsfp", 0,
                Subsignal("txp", Pins("N9")),
                Subsignal("txn", Pins("N8")),
                Subsignal("rxp", Pins("N4")),
                Subsignal("rxn", Pins("N3"))
            )
        ])

        self.ethphy = USP_GTY_1000BASEX(self.crg.cd_eth.clk,
            data_pads    = self.platform.request("qsfp", 0),
            sys_clk_freq = self.clk_freq)
        self.add_etherbone(phy=self.ethphy, buffer_depth=255)

        # SRAM -------------------------------------------------------------------------------------
        self.add_ram("sram", 0x20000000, 0x1000)

        # Leds -------------------------------------------------------------------------------------
        from litex.soc.cores.led import LedChaser
        self.leds = LedChaser(
            pads         = platform.request_all("user_led"),
            sys_clk_freq = sys_clk_freq
        )

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth Bench on XCU1525")
    parser.add_argument("--build",       action="store_true", help="Build bitstream")
    parser.add_argument("--load",        action="store_true", help="Load bitstream")
    args = parser.parse_args()

    soc     = BenchSoC()
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
