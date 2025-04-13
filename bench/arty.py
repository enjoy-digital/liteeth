#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2020-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Once built, this example can be interacted with using the `bench/test_udp_streamer.py` script

import os
import argparse

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from litex_boards.platforms import digilent_arty
from litex_boards.targets.digilent_arty import _CRG

from litex.soc.cores.clock import *
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from liteeth.phy.mii import LiteEthPHYMII

# Bench SoC ----------------------------------------------------------------------------------------

class BenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(50e6)):
        platform = digilent_arty.Platform()

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident          = "LiteEth bench on Arty",
            ident_version  = True
        )

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # Etherbone --------------------------------------------------------------------------------
        self.ethphy = LiteEthPHYMII(
            clock_pads = self.platform.request("eth_clocks"),
            pads       = self.platform.request("eth"),
            with_hw_init_reset = False)
        self.add_etherbone(phy=self.ethphy, buffer_depth=255)

        # SRAM -------------------------------------------------------------------------------------
        self.add_ram("sram", 0x20000000, 0x1000)

        # UDP Streamer -----------------------------------------------------------------------------

        # NOTE: if you want the streamer to be clocked from `sys`, you need to create a
        # `ClockDomain` with a different name. This is because `sys` gets renamed inside
        # `LiteEthUDPCrossbar` into `eth_*`
        # Here we use `etherbone` which is created by `SoCCore.add_etherbone()`.
        from liteeth.frontend.stream import LiteEthUDPStreamer
        self.udp_streamer = udp_streamer = LiteEthUDPStreamer(
            udp        = self.ethcore_etherbone.udp,
            ip_address = "192.168.1.100",
            udp_port   = 6000,
            data_width = 8,
            cd         = "etherbone",
        )

        # Leds -------------------------------------------------------------------------------------
        leds_pads = platform.request_all("user_led")

        # Led Chaser (Default).
        chaser_leds = Signal(len(leds_pads))
        self.leds = LedChaser(
            pads         = chaser_leds,
            sys_clk_freq = sys_clk_freq)

        # Led Control from UDP Streamer RX.
        udp_leds = Signal(len(leds_pads))
        self.comb += udp_streamer.source.ready.eq(1)
        self.sync += If(udp_streamer.rx.source.valid,
            udp_leds.eq(udp_streamer.source.data)
        )

        # Led Mux: Switch to received UDP value for 1s then switch back to Led Chaser.
        self.leds_timer = leds_timer = WaitTimer(sys_clk_freq)
        self.comb += [
            leds_timer.wait.eq(~udp_streamer.rx.source.valid), # Reload Timer on new UDP value.
            If(leds_timer.done,
                leds_pads.eq(chaser_leds)
            ).Else(
                leds_pads.eq(udp_leds)
            )
        ]

        # Switches ---------------------------------------------------------------------------------

        # Resynchronize Swiches inputs.
        switches_pads = platform.request_all("user_sw")
        switches      = Signal(len(switches_pads))
        self.specials += MultiReg(switches_pads, switches)

        # Send Switches value on UDP Streamer TX every 100ms.
        switches_timer = WaitTimer(100e-3*sys_clk_freq)
        switches_fsm   = FSM(reset_state="IDLE")
        self.submodules += switches_timer, switches_fsm
        switches_fsm.act("IDLE",
            switches_timer.wait.eq(1),
            If(switches_timer.done,
                NextState("SEND-SWITCHES")
            ),
        )
        switches_fsm.act("SEND-SWITCHES",
            udp_streamer.sink.valid.eq(1),
            udp_streamer.sink.data.eq(switches),
            udp_streamer.sink.last.eq(1),
            If(udp_streamer.sink.ready,
                NextState("IDLE")
            ),
        )

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth Bench on Arty A7")
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
