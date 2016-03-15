#!/usr/bin/env python3

import argparse

from litex.gen import *

from litex.build.generic_platform import *
from litex.build.xilinx.platform import XilinxPlatform

from litex.soc.interconnect import wishbone
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.common import *

from liteeth.phy.mii import LiteEthPHYMII
from liteeth.phy.rmii import LiteEthPHYRMII
from liteeth.phy.gmii import LiteEthPHYGMII
from liteeth.phy.s7rgmii import LiteEthPHYRGMII

from liteeth.core.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore

_io = [
    ("sys_clock", 0, Pins(1)),
    ("sys_reset", 1, Pins(1)),

    # MII PHY Pads
    ("mii_eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1)),
    ),
    ("mii_eth", 0,
        Subsignal("rst_n", Pins(1)),
        Subsignal("mdio", Pins(1)),
        Subsignal("mdc", Pins(1)),
        Subsignal("dv", Pins(1)),
        Subsignal("rx_er", Pins(1)),
        Subsignal("rx_data", Pins(4)),
        Subsignal("tx_en", Pins(4)),
        Subsignal("tx_data", Pins(4)),
        Subsignal("col", Pins(1)),
        Subsignal("crs", Pins(1))
    ),

    # RMII PHY Pads
    ("rmii_eth_clocks", 0,
        Subsignal("ref_clk", Pins(1))
    ),
    ("rmii_eth", 0,
        Subsignal("rst_n", Pins(1)),
        Subsignal("rx_data", Pins(2)),
        Subsignal("crs_dv", Pins(1)),
        Subsignal("tx_en", Pins(1)),
        Subsignal("tx_data", Pins(2)),
        Subsignal("mdc", Pins(1)),
        Subsignal("mdio", Pins(1)),
    ),

    # GMII PHY Pads
    ("gmii_eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("gtx", Pins(1)),
        Subsignal("rx", Pins(1))
    ),
    ("gmii_eth", 0,
        Subsignal("rst_n", Pins(1)),
        Subsignal("int_n", Pins(1)),
        Subsignal("mdio", Pins(1)),
        Subsignal("mdc", Pins(1)),
        Subsignal("dv", Pins(1)),
        Subsignal("rx_er", Pins(1)),
        Subsignal("rx_data", Pins(8)),
        Subsignal("tx_en", Pins(1)),
        Subsignal("tx_er", Pins(1)),
        Subsignal("tx_data", Pins(8)),
        Subsignal("col", Pins(1)),
        Subsignal("crs", Pins(1))
    ),

    # RGMII PHY Pads
    ("rgmii_eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1))
    ),
    ("rgmii_eth", 0,
        Subsignal("rst_n", Pins(1)),
        Subsignal("int_n", Pins(1)),
        Subsignal("mdio", Pins(1)),
        Subsignal("mdc", Pins(1)),
        Subsignal("rx_ctl", Pins(1)),
        Subsignal("rx_data", Pins(4)),
        Subsignal("tx_ctl", Pins(1)),
        Subsignal("tx_data", Pins(4))
    ),

    # Wishbone
    ("wishbone", 0,
        Subsignal("adr",   Pins(30)),
        Subsignal("dat_r", Pins(32)),
        Subsignal("dat_w", Pins(32)),
        Subsignal("sel",   Pins(4)),
        Subsignal("cyc",   Pins(1)),
        Subsignal("stb",   Pins(1)),
        Subsignal("ack",   Pins(1)),
        Subsignal("we",    Pins(1)),
        Subsignal("cti",   Pins(3)),
        Subsignal("bte",   Pins(2)),
        Subsignal("err",   Pins(1))
    ),

    # UDP
    ("udp_sink", 0,
        Subsignal("stb",   Pins(1)),
        Subsignal("sop",   Pins(1)),
        Subsignal("eop",   Pins(1)),
        Subsignal("ack",   Pins(1)),
        # param
        Subsignal("src_port", Pins(16)),
        Subsignal("dst_port", Pins(16)),
        Subsignal("ip_address", Pins(32)),
        Subsignal("length", Pins(16)),
        # payload
        Subsignal("data", Pins(32)),
        Subsignal("error", Pins(4))
    ),

    ("udp_source", 0,
        Subsignal("stb",   Pins(1)),
        Subsignal("sop",   Pins(1)),
        Subsignal("eop",   Pins(1)),
        Subsignal("ack",   Pins(1)),
        # param
        Subsignal("src_port", Pins(16)),
        Subsignal("dst_port", Pins(16)),
        Subsignal("ip_address", Pins(32)),
        Subsignal("length", Pins(16)),
        # payload
        Subsignal("data", Pins(32)),
        Subsignal("error", Pins(4))
    ),
]

class CorePlatform(XilinxPlatform):
    name = "core"
    def __init__(self):
        XilinxPlatform.__init__(self, "xc7", _io)

    def do_finalize(self, *args, **kwargs):
        pass


class PHYCore(SoCCore):
    def __init__(self, phy, clk_freq):
        platform = CorePlatform()
        SoCCore.__init__(self, platform,
            clk_freq=clk_freq,
            cpu_type=None,
            integrated_rom_size=0x0,
            integrated_sram_size=0x0,
            integrated_main_ram_size=0x0,
            csr_address_width=14, csr_data_width=8,
            with_uart=False, with_timer=False)
        self.submodules.crg = CRG(platform.request("sys_clock"),
                                  platform.request("sys_reset"))
        # ethernet
        if phy == "MII":
            self.submodules.ethphy = LiteEthPHYMII(platform.request("mii_eth_clocks"),
                                                   platform.request("mii_eth"))
        elif phy == "RMII":
            self.submodules.ethphy = LiteEthPHYRMII(platform.request("rmii_eth_clocks"),
                                                    platform.request("rmii_eth"))
        elif phy == "GMII":
            self.submodules.ethphy = LiteEthPHYGMII(platform.request("gmii_eth_clocks"),
                                                    platform.request("gmii_eth"))
        elif phy == "RGMII":
            self.submodules.ethphy = LiteEthPHYRGMII(platform.request("rgmii_eth_clocks"),
                                                     platform.request("rgmii_eth"))
        else:
            ValueError("Unsupported " + phy + " PHY");


class MACCore(PHYCore):
    csr_peripherals = (
        "ethphy",
        "ethmac"
    )
    csr_map = dict((n, v) for v, n in enumerate(csr_peripherals, start=16))
    csr_map.update(SoCCore.csr_map)

    interrupt_map = {
        "ethmac": 2,
    }
    interrupt_map.update(SoCCore.interrupt_map)

    mem_map = {
        "ethmac": 0x50000000
    }
    mem_map.update(SoCCore.mem_map)

    def __init__(self, phy, clk_freq):
        PHYCore.__init__(self, phy, clk_freq)

        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone")
        self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"] | self.shadow_base, 0x2000)

        class _WishboneBridge(Module):
            def __init__(self, interface):
                self.wishbone = interface

        self.add_cpu_or_bridge(_WishboneBridge(self.platform.request("wishbone")))
        self.add_wb_master(self.cpu_or_bridge.wishbone)


class UDPCore(PHYCore):
    def __init__(self, phy, clk_freq, mac_address, ip_address, port):
        PHYCore.__init__(self, phy, clk_freq)

        self.submodules.core = LiteEthUDPIPCore(self.ethphy, mac_address, convert_ip(ip_address), clk_freq)
        udp_port = self.core.udp.crossbar.get_port(port, 8)
        # XXX avoid manual connect
        udp_sink = self.platform.request("udp_sink")
        self.comb += [
            # control
            udp_port.sink.stb.eq(udp_sink.stb),
            udp_port.sink.sop.eq(udp_sink.sop),
            udp_port.sink.eop.eq(udp_sink.eop),
            udp_sink.ack.eq(udp_port.sink.ack),

            # param
            udp_port.sink.src_port.eq(udp_sink.src_port),
            udp_port.sink.dst_port.eq(udp_sink.dst_port),
            udp_port.sink.ip_address.eq(udp_sink.ip_address),
            udp_port.sink.length.eq(udp_sink.length),

            # payload
            udp_port.sink.data.eq(udp_sink.data),
            udp_port.sink.error.eq(udp_sink.error)
        ]
        udp_source = self.platform.request("udp_source")
        self.comb += [
            # control
            udp_source.stb.eq(udp_port.source.stb),
            udp_source.sop.eq(udp_port.source.sop),
            udp_source.eop.eq(udp_port.source.eop),
            udp_port.source.ack.eq(udp_source.ack),

            # param
            udp_source.src_port.eq(udp_port.source.src_port),
            udp_source.dst_port.eq(udp_port.source.dst_port),
            udp_source.ip_address.eq(udp_port.source.ip_address),
            udp_source.length.eq(udp_port.source.length),

            # payload
            udp_source.data.eq(udp_port.source.data),
            udp_source.error.eq(udp_port.source.error)
        ]


def main():
    parser = argparse.ArgumentParser(description="LiteEth core builder")
    builder_args(parser)
    soc_core_args(parser)
    parser.add_argument("--phy", default="MII", help="Ethernet PHY(MII/RMII/GMII/RMGII)")
    parser.add_argument("--core", default="wishbone", help="Ethernet Core(wishbone/udp)")
    parser.add_argument("--mac_address", default=0x10e2d5000000, help="MAC address")
    parser.add_argument("--ip_address", default="192.168.1.50", help="IP address")
    args = parser.parse_args()

    if args.core == "mac":
        soc = MACCore(phy=args.phy, clk_freq=100*1000000)
    elif args.core == "udp":
        soc =  UDPCore(phy=args.phy, clk_freq=100*10000000,
                       mac_address=args.mac_address,
                       ip_address=args.ip_address,
                       port=6000)
    else:
        raise ValueError
    builder = Builder(soc, output_dir="liteeth", compile_gateware=False, csr_csv="liteeth/csr.csv")
    builder.build(build_name="liteeth")

if __name__ == "__main__":
    main()

