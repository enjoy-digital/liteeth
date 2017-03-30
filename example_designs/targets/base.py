from litex.gen.genlib.io import CRG

from litex.build.xilinx.vivado import XilinxVivadoToolchain
from litex.soc.interconnect import wishbone

from litex.soc.integration.soc_core import SoCCore
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from liteeth.common import *
from liteeth.phy import LiteEthPHY
from liteeth.core import LiteEthUDPIPCore


class BaseSoC(SoCCore):
    csr_map = {
        "phy":  11,
        "core": 12
    }
    csr_map.update(SoCCore.csr_map)
    def __init__(self, platform, clk_freq=166*1000000,
            mac_address=0x10e2d5000000,
            ip_address="192.168.1.50"):
        clk_freq = int((1/(platform.default_clk_period))*1000000000)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="LiteEth Base Design",
            with_timer=False
        )
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"), clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))

        # wishbone SRAM (to test Wishbone over UART and Etherbone)
        self.submodules.sram = wishbone.SRAM(1024)
        self.add_wb_slave(lambda a: a[23:25] == 1, self.sram.bus)

        # ethernet PHY and UDP/IP stack
        self.submodules.phy = LiteEthPHY(platform.request("eth_clocks"), platform.request("eth"), clk_freq=clk_freq)
        self.submodules.core = LiteEthUDPIPCore(self.phy, mac_address, convert_ip(ip_address), clk_freq)

        if isinstance(platform.toolchain, XilinxVivadoToolchain):
            self.crg.cd_sys.clk.attr.add("keep")
            self.phy.crg.cd_eth_rx.clk.attr.add("keep")
            self.phy.crg.cd_eth_tx.clk.attr.add("keep")
            platform.add_platform_command("""
create_clock -name sys_clk -period 6.0 [get_nets sys_clk]
create_clock -name eth_rx_clk -period 8.0 [get_nets eth_rx_clk]
create_clock -name eth_tx_clk -period 8.0 [get_nets eth_tx_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_rx_clk]
set_false_path -from [get_clocks eth_rx_clk] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_tx_clk]
set_false_path -from [get_clocks eth_tx_clk] -to [get_clocks sys_clk]
""")


class BaseSoCDevel(BaseSoC):
    csr_map = {
        "analyzer": 20
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        BaseSoC.__init__(self, platform)

        self.core_icmp_rx_fsm_state = Signal(4)
        self.core_icmp_tx_fsm_state = Signal(4)
        self.core_udp_rx_fsm_state = Signal(4)
        self.core_udp_tx_fsm_state = Signal(4)
        self.core_ip_rx_fsm_state = Signal(4)
        self.core_ip_tx_fsm_state = Signal(4)
        self.core_arp_rx_fsm_state = Signal(4)
        self.core_arp_tx_fsm_state = Signal(4)
        self.core_arp_table_fsm_state = Signal(4)

        debug = [
            # MAC interface
            self.core.mac.core.sink.valid,
            self.core.mac.core.sink.last,
            self.core.mac.core.sink.ready,
            self.core.mac.core.sink.data,

            self.core.mac.core.source.valid,
            self.core.mac.core.source.last,
            self.core.mac.core.source.ready,
            self.core.mac.core.source.data,

            # ICMP interface
            self.core.icmp.echo.sink.valid,
            self.core.icmp.echo.sink.last,
            self.core.icmp.echo.sink.ready,
            self.core.icmp.echo.sink.data,

            self.core.icmp.echo.source.valid,
            self.core.icmp.echo.source.last,
            self.core.icmp.echo.source.ready,
            self.core.icmp.echo.source.data,

            # IP interface
            self.core.ip.crossbar.master.sink.valid,
            self.core.ip.crossbar.master.sink.last,
            self.core.ip.crossbar.master.sink.ready,
            self.core.ip.crossbar.master.sink.data,
            self.core.ip.crossbar.master.sink.ip_address,
            self.core.ip.crossbar.master.sink.protocol,

            # State machines
            self.core_icmp_rx_fsm_state,
            self.core_icmp_tx_fsm_state,

            self.core_arp_rx_fsm_state,
            self.core_arp_tx_fsm_state,
            self.core_arp_table_fsm_state,

            self.core_ip_rx_fsm_state,
            self.core_ip_tx_fsm_state,

            self.core_udp_rx_fsm_state,
            self.core_udp_tx_fsm_state
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(debug, 4096)

    def do_finalize(self):
        BaseSoC.do_finalize(self)
        self.comb += [
            self.core_icmp_rx_fsm_state.eq(self.core.icmp.rx.fsm.state),
            self.core_icmp_tx_fsm_state.eq(self.core.icmp.tx.fsm.state),

            self.core_arp_rx_fsm_state.eq(self.core.arp.rx.fsm.state),
            self.core_arp_tx_fsm_state.eq(self.core.arp.tx.fsm.state),
            self.core_arp_table_fsm_state.eq(self.core.arp.table.fsm.state),

            self.core_ip_rx_fsm_state.eq(self.core.ip.rx.fsm.state),
            self.core_ip_tx_fsm_state.eq(self.core.ip.tx.fsm.state),

            self.core_udp_rx_fsm_state.eq(self.core.udp.rx.fsm.state),
            self.core_udp_tx_fsm_state.eq(self.core.udp.tx.fsm.state)
        ]

    def do_exit(self, vns):
        self.analyzer.export_csv(vns, "test/analyzer.csv")

default_subtarget = BaseSoC
