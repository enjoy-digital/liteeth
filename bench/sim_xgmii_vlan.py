from liteeth.phy.xgmii import LiteEthPHYXGMII
from liteeth.core import LiteEthVLANUDPIPCore
from liteeth.common import convert_ip
from litex.tools.litex_sim import *


class VLANSim(SimSoC):
    def add_tx_test(self, cd, udp_port, dst_ip, xg_counter, dw=64, always_xmit=True):
        send_pkt = Signal(reset=0)
        if always_xmit:
            send_pkt_counter_d = Signal()
            cd += [
                send_pkt_counter_d.eq(xg_counter[18]),
                send_pkt.eq(send_pkt_counter_d ^ xg_counter[18])
            ]

        bytes_per_word = dw // 8
        sink_counter = Signal(16)
        SINK_LENGTH = 8 * bytes_per_word           # 8 words
        shift = log2_int(bytes_per_word)  # bits required to represent bytes per word
        words_per_packet = SINK_LENGTH >> shift
        # Note the clkmgt domain
        cd += [
            If(send_pkt,
               sink_counter.eq(words_per_packet)),
            If((sink_counter > 0) & (udp_port.sink.ready == 1),
               sink_counter.eq(sink_counter - 1)
            ).Else(
                udp_port.sink.valid.eq(0),
                udp_port.sink.last.eq(0)
            ),
            udp_port.sink.valid.eq(sink_counter > 0),
            udp_port.sink.last.eq(sink_counter == 1),
            If(sink_counter == 1,
               udp_port.sink.last_be.eq(0x80)
            ).Else(
               udp_port.sink.last_be.eq(0x0)
            )
        ]

        self.comb += [
            # param
            udp_port.sink.src_port.eq(3000),
            udp_port.sink.dst_port.eq(7778),
            udp_port.sink.ip_address.eq(convert_ip(dst_ip)),
            udp_port.sink.length.eq(SINK_LENGTH),

            # payload
            udp_port.sink.data.eq(Cat(0xc0ffeec1ffee, sink_counter)),
            udp_port.sink.error.eq(0)
        ]

    def __init__(self, phy_model, host_ip="192.168.2.100", host_udp_port=2000, **soc_kwargs):
        SimSoC.__init__(self,
            cpu_type              = None,
            integrated_rom_size   = 0x10000,
            uart_name             = "sim",
            with_sdram            = False,
            with_ethernet         = False,
            with_etherbone        = False,
            etherbone_mac_address = 0x10e2d5000001,
            etherbone_ip_address  = "192.168.2.50",
            sdram_module          = "MT48LC16M16",
            sdram_data_width      = 8,
            with_sdcard           = False,
        )

        DW = 64 if phy_model == "xgmii" else 8
        if DW == 64:
            self.submodules.ethphy = LiteEthPHYXGMII(None, self.platform.request("xgmii_eth", 0), model=True)
        else:
            self.submodules.ethphy = LiteEthPHYGMII(None, self.platform.request("gmii_eth", 0), model=True)
        self.submodules.udp_core = LiteEthVLANUDPIPCore(self.ethphy,
                                                        0x10e2d5000001,
                                                        convert_ip("192.168.2.50"),
                                                        self.sys_clk_freq,
                                                        with_ip_broadcast=False,
                                                        dw=DW)
        udp_core = self.udp_core.add_vlan(vlan_ip="192.168.3.50", vlan_id=2001)
        udp_port0 = udp_core.crossbar.get_port(3000, DW)
        counter = Signal(28)
        self.sync += counter.eq(counter+1)
        self.add_tx_test(self.sync, udp_port0, "192.168.3.100", counter, dw=DW)

        udp_core = self.udp_core.add_vlan(vlan_ip="192.168.4.50", vlan_id=2002)
        udp_port1 = udp_core.crossbar.get_port(3000, DW)
        self.add_tx_test(self.sync, udp_port1, "192.168.4.100", counter, dw=DW)


def main():
    from litex.soc.integration.soc import LiteXSoCArgumentParser
    parser = LiteXSoCArgumentParser(description="LiteX SoC Simulation utility")
    parser.set_platform(SimPlatform)
    sim_args(parser)
    args = parser.parse_args()

    soc_kwargs              = soc_core_argdict(args)

    sys_clk_freq = int(1e6)
    sim_config   = SimConfig()
    sim_config.add_clocker("sys_clk", freq_hz=sys_clk_freq)
    sim_config.add_module("serial2console", "serial")
    if args.ethernet_phy_model == "xgmii":
        sim_config.add_module("xgmii_ethernet", "xgmii_eth", args={"interface": "tap0", "ip": "192.168.2.100"})
    elif args.ethernet_phy_model == "gmii":
        sim_config.add_module("gmii_ethernet", "gmii_eth", args={"interface": "tap0", "ip": "192.168.2.100"})

    # SoC ------------------------------------------------------------------------------------------
    soc = VLANSim(args.ethernet_phy_model, **soc_kwargs)

    def pre_run_callback(vns):
        if args.trace:
            generate_gtkw_savefile(builder, vns, args.trace_fst)

    # Build/Run ------------------------------------------------------------------------------------
    builder = Builder(soc, **parser.builder_argdict)
    builder.build(sim_config=sim_config,
                  interactive      = not args.non_interactive,
                  pre_run_callback = pre_run_callback,
                  **parser.toolchain_argdict,
    )


# Allows you to test on Debian like system:
# sudo ip link add link tap0 name tap0.2001 type vlan id 2001 && sudo ip addr add 192.168.3.100/24 dev tap0.2001 && sudo ip link set dev tap0.2001 up && sudo ip link add link tap0 name tap0.2002 type vlan id 2002 && sudo ip addr add 192.168.4.100/24 dev tap0.2002 && sudo ip link set dev tap0.2002 up && ip a && sudo tcpdump -i tap0

if __name__ == "__main__":
    main()
