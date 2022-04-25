#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2022 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import argparse

from migen import *

from litex_boards.platforms import gsd_butterstick
from litex_boards.targets.gsd_butterstick import _CRG

from litex.soc.cores.clock import *
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.phy.ecp5rgmii import LiteEthPHYRGMII

# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(50e6)):
        platform = gsd_butterstick.Platform()

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident          = "LiteEth bench on Butterstick",
            ident_version  = True
        )

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # Etherbone --------------------------------------------------------------------------------
        self.submodules.ethphy = LiteEthPHYRGMII(
            clock_pads = self.platform.request("eth_clocks"),
            pads       = self.platform.request("eth"),
            tx_delay   = 0e-9,
            with_hw_init_reset = False)
        self.add_etherbone(phy=self.ethphy, buffer_depth=255)

        # SRAM -------------------------------------------------------------------------------------
        self.add_ram("sram", 0x20000000, 0x1000)

        # Leds -------------------------------------------------------------------------------------
        from litex.soc.cores.led import LedChaser
        self.comb += platform.request("user_led_color").eq(0b010) # Blue.
        self.submodules.leds = LedChaser(
            pads         = platform.request_all("user_led"),
            sys_clk_freq = sys_clk_freq
        )

        # JTAGBone ---------------------------------------------------------------------------------
        self.add_jtagbone()

        # Analyzer ---------------------------------------------------------------------------------
        from litescope import LiteScopeAnalyzer
        ethcore   = self.ethcore_etherbone
        etherbone = self.etherbone
        self.submodules.analyzer = LiteScopeAnalyzer([
            # MAC.
            ethcore.mac.core.sink.valid,
            ethcore.mac.core.sink.ready,
            ethcore.mac.core.source.valid,
            ethcore.mac.core.source.ready,

            # ARP.
            ethcore.arp.rx.sink.valid,
            ethcore.arp.rx.sink.ready,
            ethcore.arp.tx.source.valid,
            ethcore.arp.tx.source.ready,

            # IP.
            ethcore.ip.rx.sink.valid,
            ethcore.ip.rx.sink.ready,
            ethcore.ip.tx.source.valid,
            ethcore.ip.tx.source.ready,

            # UDP.
            ethcore.udp.rx.sink.valid,
            ethcore.udp.rx.sink.ready,
            ethcore.udp.tx.source.valid,
            ethcore.udp.tx.source.ready,

            # Etherbone.
            etherbone.packet.rx.sink.valid,
            etherbone.packet.rx.sink.ready,
            etherbone.packet.rx.fsm,
            etherbone.packet.tx.source.valid,
            etherbone.packet.tx.source.ready,
            etherbone.packet.tx.fsm,
            etherbone.record.receiver.fsm,
            etherbone.record.sender.fsm
        ],
        depth=512)

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth Bench on Butterstick")
    parser.add_argument("--build", action="store_true", help="Build bitstream")
    parser.add_argument("--load",  action="store_true", help="Load bitstream")
    args = parser.parse_args()

    soc     = BenchSoC()
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()
