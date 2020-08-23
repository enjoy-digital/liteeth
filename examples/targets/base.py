#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.build.io import CRG
from litex.build.xilinx.vivado import XilinxVivadoToolchain
from litex.soc.interconnect import wishbone

from litex.soc.integration.soc_core import SoCCore
from litex.soc.cores.uart import UARTWishboneBridge

from liteeth.common import *
from liteeth.phy import LiteEthPHY
from liteeth.core import LiteEthUDPIPCore

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, platform, clk_freq=int(166e6),
            mac_address = 0x10e2d5000000,
            ip_address  = "192.168.1.50"):
        sys_clk_freq = int((1/(platform.default_clk_period))*1e9)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type       = None,
            csr_data_width = 32,
            with_uart      = False,
            ident          = "LiteEth Base Design",
            with_timer     = False
        )

        # Serial Wishbone Bridge
        serial_bridge = UARTWishboneBridge(platform.request("serial"), sys_clk_freq, baudrate=115200)
        self.submodules += serial_bridge
        self.add_wb_master(serial_bridge.wishbone)
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))

        # Ethernet PHY and UDP/IP stack
        self.submodules.ethphy  = ethphy = LiteEthPHY(
            clock_pads = platform.request("eth_clocks"),
            pads       = platform.request("eth"),
            clk_freq   = clk_freq)
        self.add_csr("ethphy")
        self.submodules.ethcore = ethcore = LiteEthUDPIPCore(
            phy         = ethphy,
            mac_address = mac_address,
            ip_address  = ip_address,
            clk_freq    = clk_freq)
        self.add_csr("ethcore")

        if isinstance(platform.toolchain, XilinxVivadoToolchain):
            self.crg.cd_sys.clk.attr.add("keep")
            ethphy.crg.cd_eth_rx.clk.attr.add("keep")
            ethphy.crg.cd_eth_tx.clk.attr.add("keep")
            platform.add_period_constraint(self.ethphy.crg.cd_eth_rx.clk, 1e9/125e6)
            platform.add_period_constraint(self.ethphy.crg.cd_eth_tx.clk, 1e9/125e6)
            platform.add_false_path_constraints(
                self.crg.cd_sys.clk,
                ethphy.crg.cd_eth_rx.clk,
                ethphy.crg.cd_eth_tx.clk)

# BaseSoCDevel -------------------------------------------------------------------------------------

class BaseSoCDevel(BaseSoC):
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        BaseSoC.__init__(self, platform)

        analyzer_signals = [
            # MAC interface
            self.ethcore.mac.core.sink.valid,
            self.ethcore.mac.core.sink.last,
            self.ethcore.mac.core.sink.ready,
            self.ethcore.mac.core.sink.data,

            self.ethcore.mac.core.source.valid,
            self.ethcore.mac.core.source.last,
            self.ethcore.mac.core.source.ready,
            self.ethcore.mac.core.source.data,

            # ICMP interface
            self.ethcore.icmp.echo.sink.valid,
            self.ethcore.icmp.echo.sink.last,
            self.ethcore.icmp.echo.sink.ready,
            self.ethcore.icmp.echo.sink.data,

            self.ethcore.icmp.echo.source.valid,
            self.ethcore.icmp.echo.source.last,
            self.ethcore.icmp.echo.source.ready,
            self.ethcore.icmp.echo.source.data,

            # IP interface
            self.ethcore.ip.crossbar.master.sink.valid,
            self.ethcore.ip.crossbar.master.sink.last,
            self.ethcore.ip.crossbar.master.sink.ready,
            self.ethcore.ip.crossbar.master.sink.data,
            self.ethcore.ip.crossbar.master.sink.ip_address,
            self.ethcore.ip.crossbar.master.sink.protocol,

            # State machines
            self.ethcore.icmp.rx.fsm,
            self.ethcore.icmp.tx.fsm,

            self.ethcore.arp.rx.fsm,
            self.ethcore.arp.tx.fsm,
            self.ethcore.arp.table.fsm,

            self.ethcore.ip.rx.fsm,
            self.ethcore.ip.tx.fsm,

            self.ethcore.udp.rx.fsm,
            self.ethcore.udp.tx.fsm
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 4096, csr_csv="test/analyzer.csv")
        self.add_csr("analyzer")

default_subtarget = BaseSoC
