#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2022-2025 Charles-Henri Mousset <ch.mousset@gmail.com>
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import argparse

from migen import *

from litex.gen import *

from litex.build.generic_platform import *

from litex_boards.platforms import digilent_arty
from litex_boards.targets.digilent_arty import _CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.phy.serial.baset import LiteEthPHY10BASET

# 10BASE-T on PMOD B -------------------------------------------------------------------------------
#
# The transmit pair is driven by two LVCMOS33 outputs, the receive pair is sampled by an LVDS input.
# Each pair needs a 100 Ohm termination, two 1k bias resistors and two 1uF series capacitors (or a
# transformer). The bias resistors go to IOs (driven low/high) to keep the wiring on the PMOD.
#
# +───────PMOD Pinout───────+
# | 7  td_p_pd | td_p     1 |
# | 8  td_n_pu | td_n     2 |
# | 9  rd_p_pd | rd_p     3 |
# | 10 rd_n_pu | rd_n     4 |
# | 11     GND | GND      5 |
# | 12     3V3 | 3V3      6 |
# +────────────+────────────+
#
#           ___
# td_p_pd──|___|───td_p────||────RJ45 3
#           1k0     |      1u
#                   ─
#                  | | 100
#                   ─
#           ___     |
# td_n_pu──|___|───td_n────||────RJ45 6
#           1k0            1u
#
#           ___
# rd_p_pd──|___|───rd_p────||────RJ45 1
#           1k0     |      1u
#                   ─
#                  | | 100
#                   ─
#           ___     |
# rd_n_pu──|___|───rd_n────||────RJ45 2
#           1k0            1u
#
# The FPGA transmits on RJ45 3/6 and receives on 1/2 (MDI-X, as a switch port): connect it to a NIC
# with a straight cable, or to an auto-MDIX switch port. The receive pair polarity is detected and
# corrected by the PHY.

_baset_io = [
    ("baset", 0,
        Subsignal("td_p",    Pins("pmodb:0"), IOStandard("LVCMOS33")),
        Subsignal("td_n",    Pins("pmodb:1"), IOStandard("LVCMOS33")),
        Subsignal("rd_p",    Pins("pmodb:2"), IOStandard("LVDS_25")),
        Subsignal("rd_n",    Pins("pmodb:3"), IOStandard("LVDS_25")),
        Subsignal("td_p_pd", Pins("pmodb:4"), IOStandard("LVCMOS33")),
        Subsignal("td_n_pu", Pins("pmodb:5"), IOStandard("LVCMOS33")),
        Subsignal("rd_p_pd", Pins("pmodb:6"), IOStandard("LVCMOS33")),
        Subsignal("rd_n_pu", Pins("pmodb:7"), IOStandard("LVCMOS33")),
    ),
]

# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(100e6), baset_clk_freq=int(100e6),
        ip_address = "192.168.1.50"):
        platform = digilent_arty.Platform()
        platform.add_extension(_baset_io)

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident          = "LiteEth 10BASE-T bench on Arty",
            ident_version  = True,
        )

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq, with_dram=False)
        self.crg.cd_baset = ClockDomain()
        self.crg.pll.create_clkout(self.crg.cd_baset, baset_clk_freq)

        # UARTBone (Host access when the Ethernet link is not up) ----------------------------------
        self.add_uartbone()

        # Etherbone over 10BASE-T ------------------------------------------------------------------
        pads = platform.request("baset")
        self.comb += [
            pads.td_p_pd.eq(0),
            pads.td_n_pu.eq(1),
            pads.rd_p_pd.eq(0),
            pads.rd_n_pu.eq(1),
        ]
        self.ethphy = LiteEthPHY10BASET(pads,
            clk_freq           = baset_clk_freq,
            refclk_cd          = "baset",
            with_hw_init_reset = False,
        )
        self.add_etherbone(phy=self.ethphy, ip_address=ip_address)

        # SRAM -------------------------------------------------------------------------------------
        self.add_ram("sram", 0x20000000, 0x1000)

        # Leds -------------------------------------------------------------------------------------
        leds = platform.request_all("user_led")
        self.comb += [
            leds[0].eq(self.ethphy.link_up),
            leds[1].eq(self.ethphy._status.fields.inverted),
        ]

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth 10BASE-T Bench on Arty A7")
    parser.add_argument("--build",      action="store_true",     help="Build bitstream.")
    parser.add_argument("--load",       action="store_true",     help="Load bitstream.")
    parser.add_argument("--ip-address", default="192.168.1.50", help="Etherbone IP address.")
    args = parser.parse_args()

    soc     = BenchSoC(ip_address=args.ip_address)
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
