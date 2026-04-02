#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2020-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Build/Use:
# ----------
# Build and load the PTP bench design:
#   ./bench/arty_ptp.py --build --load
#
# Configure ptp4l as a Master on the host (E2E, UDP/IPv4, software timestamping):
#
#   Create a config file (e.g. ptp-master.cfg):
#     [global]
#     twoStepFlag            1
#     time_stamping          software
#     delay_mechanism        E2E
#     network_transport      UDPv4
#     domainNumber           0
#     logAnnounceInterval    1
#     logSyncInterval        0
#     logMinDelayReqInterval 0
#     udp_ttl                1
#
#     [eth0]
#     masterOnly             1
#
#   Replace "eth0" with your network interface name (e.g. enp6s0, tap0).
#
#   Run ptp4l:
#     sudo ptp4l -f ptp-master.cfg
#
# Monitor PTP state over Etherbone:
#   ./bench/test_ptp.py --count 100
#   ./bench/test_ptp.py --count 100 --debug
#   ./bench/test_ptp.py --count 100 --plot

import os
import argparse

from migen import *

from litex.gen import *

from litex_boards.platforms import digilent_arty

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from liteeth.phy.mii import LiteEthPHYMII
from liteeth.core.ptp import LiteEthPTP

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq):
        self.rst        = Signal()
        self.cd_sys     = ClockDomain()
        self.cd_sys_eth = ClockDomain()
        self.cd_eth     = ClockDomain()

        # # #

        # Clk/Rst.
        clk100 = platform.request("clk100")
        rst_n  = platform.request("cpu_reset_n")

        # PLL.
        self.pll = pll = S7PLL(speedgrade=-1)
        self.comb += pll.reset.eq(~rst_n | self.rst)
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys,     sys_clk_freq)
        pll.create_clkout(self.cd_sys_eth, sys_clk_freq)
        pll.create_clkout(self.cd_eth,     25e6)
        self.comb += platform.request("eth_ref_clk").eq(self.cd_eth.clk)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin)

# PTP Bench SoC ------------------------------------------------------------------------------------

class PTPBenchSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(100e6), p2p=False, ptp_debug=False):
        platform = digilent_arty.Platform()

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident          = "LiteEth PTP bench on Arty",
            ident_version  = True,
        )

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # Etherbone (with IGMP for PTP multicast) -------------------------------------------------
        self.ethphy = LiteEthPHYMII(
            clock_pads         = self.platform.request("eth_clocks"),
            pads               = self.platform.request("eth"),
            with_hw_init_reset = False,
        )
        # CDC between sys_eth and Ethernet PHY clocks.
        self.platform.add_false_path_constraints(
            self.crg.cd_sys_eth.clk,
            self.ethphy.crg.cd_eth_rx.clk,
            self.ethphy.crg.cd_eth_tx.clk,
        )
        ptp_igmp_groups = [0xE0000181, 0xE0000182]  # 224.0.1.129, 224.0.1.130.
        if p2p:
            ptp_igmp_groups.append(0xE000006B)       # 224.0.0.107.
        self.add_etherbone(phy=self.ethphy, buffer_depth=255,
            with_igmp     = True,
            igmp_groups   = ptp_igmp_groups,
            igmp_interval = 2,
        )

        # PTP --------------------------------------------------------------------------------------
        udp = self.ethcore_etherbone.udp

        # PTP event / general ports (CDC from sys_eth to ethcore clock domain).
        self.ptp_event_port   = udp.crossbar.get_port(319, dw=8, cd="sys_eth")
        self.ptp_general_port = udp.crossbar.get_port(320, dw=8, cd="sys_eth")

        # PTP core (runs in sys_eth domain).
        self.ptp = ClockDomainsRenamer("sys_eth")(LiteEthPTP(
            self.ptp_event_port,
            self.ptp_general_port,
            sys_clk_freq,
            monitor_debug = ptp_debug,
        ))

        # PTP configuration.
        self.comb += [
            self.ptp.clock_id.eq((0x10e2d5000001 << 16) | 1),
            self.ptp.p2p_mode.eq(1 if p2p else 0),
        ]

        # Leds -------------------------------------------------------------------------------------
        self.leds = LedChaser(
            pads         = platform.request_all("user_led"),
            sys_clk_freq = sys_clk_freq,
        )

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth PTP Bench on Arty A7.")
    parser.add_argument("--build",     action="store_true", help="Build bitstream.")
    parser.add_argument("--load",      action="store_true", help="Load bitstream.")
    parser.add_argument("--p2p",       action="store_true", help="Enable PTP P2P mode.")
    parser.add_argument("--ptp-debug", action="store_true", help="Enable PTP debug monitor CSRs.")
    args = parser.parse_args()

    soc     = PTPBenchSoC(p2p=args.p2p, ptp_debug=args.ptp_debug)
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
