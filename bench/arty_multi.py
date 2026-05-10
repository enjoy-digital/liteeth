#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2022 Charles-Henri Mousset <ch.mousset@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

import os
import argparse

from migen import *

from litex_boards.platforms import digilent_arty
from litex.build.xilinx.vivado import vivado_build_args, vivado_build_argdict
from litex_boards.targets.digilent_arty import _CRG, BaseSoC
from litex.build.generic_platform import *

from litex.soc.cores.clock import *
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.phy.mii import LiteEthPHYMII
from liteeth.phy.ethernet import LiteEthPHYETHERNET

from litescope import LiteScopeAnalyzer


# PMOD Raw 10BASET Ethernet ------------------------------------------------------------------------
#
# This testbench uses 2 differential pairs of the Arty-A7, and 4 outputs to bias the termination
# resistors.
# Two(2) 100 Ohms resistor (termination), four(4) 1kOhms (bias) resistors and four(4) capacitors are
# required to connect to the Ethernet port.
# Both the TX and RX pairs are wired identically.
# Using the 4 IOs as pullup/down are not strictly required (you can bias to GND/3V3 instead), but it
# makes wiring very convenient using only a piece of proto-PCB and SMD components.
#
# Ideally the capacitors should be replaced with suitable transformers, but for short
# wiring the capacitors usually work well.
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
# td_p_pd──|___|───td_p────||────ORANGE/WHITE (RJ45 3)
#           1k0     |      1u
#                   ─
#                  | | 100
#                   ─
#           ___     |
# td_n_pu──|___|───td_n────||────ORANGE (RJ45 6)
#           1k0            1u
#
#
#           ___
# rd_p_pd──|___|───rd_p────||────GREEN/WHITE (RJ45 1)
#           1k0     |      1u
#                   ─
#                  | | 100
#                   ─
#           ___     |
# rd_n_pu──|___|───rd_n────||────GREEN (RJ45 2)
#           1k0            1u
#
#
# Alternate wiring using a transformer (both channels are identical):
#           ___
# rd_p_pd──|___|───rd_p─────,      ,────GREEN/WHITE (RJ45 1)
#           1k0     |      *_) || (_*
#                   ─       _) || (_
#                  | | 100  _) || (_
#                   ─       _) || (_
#           ___     |       _) || (_
# rd_n_pu──|___|───rd_n─────'      '────GREEN (RJ45 2)
#           1k0 
#

raw_eth = [
    ("raw_eth", 0,
        Subsignal("td_p", Pins("pmodb:0"), IOStandard("LVCMOS33")),
        Subsignal("td_n", Pins("pmodb:1"), IOStandard("LVCMOS33")),
        Subsignal("rd_p", Pins("pmodb:2"), IOStandard("LVDS_25"), Misc("PULLDOWN")),
        Subsignal("rd_n", Pins("pmodb:3"), IOStandard("LVDS_25"), Misc("PULLUP")),
        Subsignal("td_p_pd", Pins("pmodb:4"), IOStandard("LVCMOS33")),
        Subsignal("td_n_pu", Pins("pmodb:5"), IOStandard("LVCMOS33")),
        Subsignal("rd_p_pd", Pins("pmodb:6"), IOStandard("LVCMOS33")),
        Subsignal("rd_n_pu", Pins("pmodb:7"), IOStandard("LVCMOS33")),
    ),
]


# Bench SoC ----------------------------------------------------------------------------------------
class BenchSoC(BaseSoC):
    def __init__(self, sys_clk_freq=int(50e6), with_raw_ethernet=True, **kwarg):
        analyzer_signals = []

        # BaseSoC ----------------------------------------------------------------------------------
        super().__init__(**kwarg
        )
        self.platform.add_extension(raw_eth)

        # Etherbone on 'raw' ethernet --------------------------------------------------------------
        if with_raw_ethernet:
            eth_raw_pads = self.platform.request("raw_eth")
            self.crg.clock_domains.cd_eth_raw = ClockDomain()
            self.crg.clock_domains.cd_eth_raw_tx = ClockDomain()
            self.crg.clock_domains.cd_eth_raw_rx = ClockDomain()
            self.comb += [
                self.crg.cd_eth_raw_rx.clk.eq(self.crg.cd_eth_raw.clk),
                self.crg.cd_eth_raw_tx.clk.eq(self.crg.cd_eth_raw.clk),
            ]
            self.crg.pll.create_clkout(self.crg.cd_eth_raw, 40e6)
            self.submodules.ethphy = ethphy = LiteEthPHYETHERNET(
                pads = eth_raw_pads, refclk_cd="eth_raw",
                with_hw_init_reset = False)
            self.add_etherbone(name="etherbone", phy=self.ethphy, buffer_depth=255,
                ip_address="192.168.2.51", phy_cd="eth_raw")
            analyzer_signals += [
                ethphy.rx.fsm,
                ethphy.tx.fsm,
                ethphy.rx.rx_i,
                ethphy.tx.tx,
            ]
            self.comb += [
                eth_raw_pads.td_p_pd.eq(0),
                eth_raw_pads.td_n_pu.eq(1),
                eth_raw_pads.rd_p_pd.eq(0),
                eth_raw_pads.rd_n_pu.eq(1),
            ]

        # Analyzer ---------------------------------------------------------------------------------
        ethcore   = self.ethcore_etherbone
        etherbone = self.etherbone
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals + [
            self.ethphy.sink,
            self.ethphy.source,
            # MAC.
            ethcore.mac.core.sink.valid,
            ethcore.mac.core.sink.ready,
            ethcore.mac.core.source.valid,
            ethcore.mac.core.source.payload,
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
        depth=1024 * 8, trigger_depth=256)


# Main ---------------------------------------------------------------------------------------------

def main():
    from litex.soc.integration.soc import LiteXSoCArgumentParser
    parser = LiteXSoCArgumentParser(description="LiteX SoC on Arty A7")
    target_group = parser.add_argument_group(title="Target options")
    target_group.add_argument("--toolchain",           default="vivado",                 help="FPGA toolchain (vivado, symbiflow or yosys+nextpnr).")
    target_group.add_argument("--build",               action="store_true",              help="Build design.")
    target_group.add_argument("--load",                action="store_true",              help="Load bitstream.")
    target_group.add_argument("--flash",               action="store_true",              help="Flash bitstream.")
    target_group.add_argument("--variant",             default="a7-35",                  help="Board variant (a7-35 or a7-100).")
    target_group.add_argument("--sys-clk-freq",        default=100e6,                    help="System clock frequency.")
    ethopts = target_group.add_mutually_exclusive_group()
    ethopts.add_argument("--with-ethernet",      action="store_true",              help="Enable Ethernet support.")
    ethopts.add_argument("--with-etherbone",     action="store_true",              help="Enable Etherbone support.")
    ethopts.add_argument("--raw-eth",            action="store_true", help="use raw 10BASET ethernet on PMODA")
    target_group.add_argument("--eth-ip",              default="192.168.1.50", type=str, help="Ethernet/Etherbone IP address.")
    target_group.add_argument("--eth-dynamic-ip",      action="store_true",              help="Enable dynamic Ethernet IP addresses setting.")
    sdopts = target_group.add_mutually_exclusive_group()
    sdopts.add_argument("--with-spi-sdcard",     action="store_true",              help="Enable SPI-mode SDCard support.")
    sdopts.add_argument("--with-sdcard",         action="store_true",              help="Enable SDCard support.")
    target_group.add_argument("--sdcard-adapter",      type=str,                         help="SDCard PMOD adapter (digilent or numato).")
    target_group.add_argument("--with-jtagbone",       action="store_true",              help="Enable JTAGbone support.")
    target_group.add_argument("--with-spi-flash",      action="store_true",              help="Enable SPI Flash (MMAPed).")
    target_group.add_argument("--with-pmod-gpio",      action="store_true",              help="Enable GPIOs through PMOD.") # FIXME: Temporary test.
    builder_args(parser)
    soc_core_args(parser)
    vivado_build_args(parser)
    args = parser.parse_args()

    assert not (args.with_etherbone and args.eth_dynamic_ip)

    soc = BenchSoC(
        variant           = args.variant,
        toolchain         = args.toolchain,
        sys_clk_freq      = int(float(args.sys_clk_freq)),
        with_ethernet     = args.with_ethernet,
        with_etherbone    = args.with_etherbone,
        eth_ip            = args.eth_ip,
        eth_dynamic_ip    = args.eth_dynamic_ip,
        with_jtagbone     = args.with_jtagbone,
        with_spi_flash    = args.with_spi_flash,
        with_pmod_gpio    = args.with_pmod_gpio,
        with_raw_ethernet = args.raw_eth,
        **soc_core_argdict(args)
    )
    if args.sdcard_adapter == "numato":
        soc.platform.add_extension(digilent_arty._numato_sdcard_pmod_io)
    else:
        soc.platform.add_extension(digilent_arty._sdcard_pmod_io)
    if args.with_spi_sdcard:
        soc.add_spi_sdcard()
    if args.with_sdcard:
        soc.add_sdcard()

    builder = Builder(soc, **builder_argdict(args))
    builder_kwargs = vivado_build_argdict(args) if args.toolchain == "vivado" else {}
    if args.build:
        builder.build(**builder_kwargs)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

    if args.flash:
        prog = soc.platform.create_programmer()
        prog.flash(0, builder.get_bitstream_filename(mode="flash"))

if __name__ == "__main__":
    main()
