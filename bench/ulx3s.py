#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2018-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2018 David Shah <dave@ds0.me>
# SPDX-License-Identifier: BSD-2-Clause

import os
import argparse
import sys

from migen import *

from litex_boards.platforms import ulx3s
from litex_boards.targets.ulx3s import _CRG

from litex.build.lattice.trellis import trellis_args, trellis_argdict

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.gpio import GPIOOut

from litedram import modules as litedram_modules
from litedram.phy import GENSDRPHY, HalfRateGENSDRPHY

from litex.build.generic_platform import *
from liteeth.phy import LiteEthPHYETHERNET


# IOs ----------------------------------------------------------------------------------------------

_eth_io = [
    # Direct connect 10BASE-T, full-duplex Ethernet
    ("eth", 0,
        Subsignal("td_p", Pins("A2")), # J1 GP9  - Green/White
        Subsignal("td_n", Pins("B1")), # J1 GN9  - Green
        Subsignal("rd_p", Pins("C4"), IOStandard("LVDS"), Misc("DIFFRESISTOR=100")), # J1 GP10 - Orange/White
        Subsignal("rd_n", Pins("B4")), # J1 GN10 - Orange
        IOStandard("LVCMOS33"),
    ),
]


# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, device="LFE5U-45F", revision="2.0", toolchain="trellis",
        sys_clk_freq=int(40e6), **kwargs):
        platform = ulx3s.Platform(device=device, revision=revision, toolchain=toolchain)
        platform.add_extension(_eth_io)

        # SoCCore ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, sys_clk_freq,
            ident          = "LiteEth bench on ULX3S",
            ident_version  = True,
            **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # Etherbone --------------------------------------------------------------------------------
        self.submodules.ethphy = LiteEthPHYETHERNET(
            pads       = self.platform.request("eth"),
            refclk_cd = "sys",
            with_hw_init_reset = False)
        self.add_csr("ethphy")
        self.add_etherbone(phy=self.ethphy, buffer_depth=255)


# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on ULX3S")
    parser.add_argument("--build",           action="store_true",   help="Build bitstream")
    parser.add_argument("--load",            action="store_true",   help="Load bitstream")
    parser.add_argument("--toolchain",       default="trellis",     help="FPGA toolchain: trellis (default) or diamond")
    parser.add_argument("--device",          default="LFE5U-45F",   help="FPGA device: LFE5U-12F, LFE5U-25F, LFE5U-45F (default)  or LFE5U-85F")
    parser.add_argument("--revision",        default="2.0",         help="Board revision: 2.0 (default) or 1.7")
    parser.add_argument("--sys-clk-freq",    default=40e6,          help="System clock frequency  (default: 50MHz)")
    builder_args(parser)
    trellis_args(parser)
    args = parser.parse_args()

    soc = BenchSoC(
        device           = args.device,
        revision         = args.revision,
        toolchain        = args.toolchain,
        sys_clk_freq     = int(float(args.sys_clk_freq)))

    builder = Builder(soc, **builder_argdict(args))
    builder_kargs = trellis_argdict(args) if args.toolchain == "trellis" else {}
    builder.build(**builder_kargs, run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + (".svf" if args.toolchain == "trellis" else ".bit")))

if __name__ == "__main__":
    main()
