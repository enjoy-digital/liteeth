#!/usr/bin/env python3

# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

"""
LiteEth standalone core generator

LiteEth aims to be directly used as a python package when the SoC is created using LiteX. However,
for some use cases it could be interesting to generate a standalone verilog file of the core:
- integration of the core in a SoC using a more traditional flow.
- need to version/package the core.
- avoid Migen/LiteX dependencies.
- etc...

The standalone core is generated from a YAML configuration file that allows the user to generate
easily a custom configuration of the core.

TODO: identify limitations
"""

import argparse

from migen import *

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

from liteeth.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore

# IOs ----------------------------------------------------------------------------------------------

_io = [
    ("sys_clock", 0, Pins(1)),
    ("sys_reset", 1, Pins(1)),

    ("interrupt", 0, Pins(1)),

    # MII PHY Pads
    ("mii_eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1)),
    ),
    ("mii_eth", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("mdio",    Pins(1)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("rx_dv",   Pins(1)),
        Subsignal("rx_er",   Pins(1)),
        Subsignal("rx_data", Pins(4)),
        Subsignal("tx_en",   Pins(1)),
        Subsignal("tx_data", Pins(4)),
        Subsignal("col",     Pins(1)),
        Subsignal("crs",     Pins(1))
    ),

    # RMII PHY Pads
    ("rmii_eth_clocks", 0,
        Subsignal("ref_clk", Pins(1))
    ),
    ("rmii_eth", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("rx_data", Pins(2)),
        Subsignal("crs_dv",  Pins(1)),
        Subsignal("tx_en",   Pins(1)),
        Subsignal("tx_data", Pins(2)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("mdio",    Pins(1)),
    ),

    # GMII PHY Pads
    ("gmii_eth_clocks", 0,
        Subsignal("tx",  Pins(1)),
        Subsignal("gtx", Pins(1)),
        Subsignal("rx",  Pins(1))
    ),
    ("gmii_eth", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("int_n",   Pins(1)),
        Subsignal("mdio",    Pins(1)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("rx_dv",   Pins(1)),
        Subsignal("rx_er",   Pins(1)),
        Subsignal("rx_data", Pins(8)),
        Subsignal("tx_en",   Pins(1)),
        Subsignal("tx_er",   Pins(1)),
        Subsignal("tx_data", Pins(8)),
        Subsignal("col",     Pins(1)),
        Subsignal("crs",     Pins(1))
    ),

    # RGMII PHY Pads
    ("rgmii_eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1))
    ),
    ("rgmii_eth", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("int_n",   Pins(1)),
        Subsignal("mdio",    Pins(1)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("rx_ctl",  Pins(1)),
        Subsignal("rx_data", Pins(4)),
        Subsignal("tx_ctl",  Pins(1)),
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
        Subsignal("valid",      Pins(1)),
        Subsignal("last",       Pins(1)),
        Subsignal("ready",      Pins(1)),
        # param
        Subsignal("src_port",   Pins(16)),
        Subsignal("dst_port",   Pins(16)),
        Subsignal("ip_address", Pins(32)),
        Subsignal("length",     Pins(16)),
        # payload
        Subsignal("data",       Pins(32)),
        Subsignal("error",      Pins(4))
    ),

    ("udp_source", 0,
        Subsignal("valid",      Pins(1)),
        Subsignal("last",       Pins(1)),
        Subsignal("ready",      Pins(1)),
        # param
        Subsignal("src_port",   Pins(16)),
        Subsignal("dst_port",   Pins(16)),
        Subsignal("ip_address", Pins(32)),
        Subsignal("length",     Pins(16)),
        # payload
        Subsignal("data",       Pins(32)),
        Subsignal("error",      Pins(4))
    ),
]

# Platform -----------------------------------------------------------------------------------------

class CorePlatform(XilinxPlatform):
    name = "core"
    def __init__(self):
        XilinxPlatform.__init__(self, "xc7", _io)

# PHY Core -----------------------------------------------------------------------------------------

class PHYCore(SoCMini):
    def __init__(self, phy, clk_freq):
        platform = CorePlatform()
        SoCMini.__init__(self, platform, clk_freq=clk_freq)
        self.submodules.crg = CRG(platform.request("sys_clock"),
                                  platform.request("sys_reset"))
        # ethernet
        if phy == "mii":
            ethphy = LiteEthPHYMII(platform.request("mii_eth_clocks"),
                                   platform.request("mii_eth"))
        elif phy == "rmii":
            ethphy = LiteEthPHYRMII(platform.request("rmii_eth_clocks"),
                                    platform.request("rmii_eth"))
        elif phy == "gmii":
            ethphy = LiteEthPHYGMII(platform.request("gmii_eth_clocks"),
                                    platform.request("gmii_eth"))
        elif phy == "rgmii":
            ethphy = LiteEthPHYRGMII(platform.request("rgmii_eth_clocks"),
                                     platform.request("rgmii_eth"))
        else:
            raise ValueError("Unsupported " + phy + " PHY");
        self.submodules.ethphy = ethphy
        self.add_csr("ethphy")

# MAC Core -----------------------------------------------------------------------------------------

class MACCore(PHYCore):
    interrupt_map = SoCCore.interrupt_map
    interrupt_map.update({
        "ethmac": 2,
    })

    mem_map = SoCCore.mem_map
    mem_map.update({
        "ethmac": 0x50000000
    })

    def __init__(self, phy, clk_freq, endianness):
        PHYCore.__init__(self, phy, clk_freq)

        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone", endianness=endianness)
        self.add_wb_slave(self.mem_map["ethmac"], self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"], 0x2000, type="io")
        self.add_csr("ethmac")

        class _WishboneBridge(Module):
            def __init__(self, interface):
                self.wishbone = interface
                self.wishbone.data_width = 32

        bridge = _WishboneBridge(self.platform.request("wishbone"))
        self.submodules += bridge
        self.add_wb_master(bridge.wishbone)

        self.comb += self.platform.request("interrupt").eq(self.ethmac.ev.irq)

# UDP Core -----------------------------------------------------------------------------------------

class UDPCore(PHYCore):
    def __init__(self, phy, clk_freq, mac_address, ip_address, port):
        PHYCore.__init__(self, phy, clk_freq)

        self.submodules.core = LiteEthUDPIPCore(self.ethphy, mac_address, convert_ip(ip_address), clk_freq)
        udp_port = self.core.udp.crossbar.get_port(port, 8)
        # XXX avoid manual connect
        udp_sink = self.platform.request("udp_sink")
        self.comb += [
            # Control
            udp_port.sink.valid.eq(udp_sink.valid),
            udp_port.sink.last.eq(udp_sink.last),
            udp_sink.ready.eq(udp_port.sink.ready),

            # Param
            udp_port.sink.src_port.eq(udp_sink.src_port),
            udp_port.sink.dst_port.eq(udp_sink.dst_port),
            udp_port.sink.ip_address.eq(udp_sink.ip_address),
            udp_port.sink.length.eq(udp_sink.length),

            # Payload
            udp_port.sink.data.eq(udp_sink.data),
            udp_port.sink.error.eq(udp_sink.error)
        ]
        udp_source = self.platform.request("udp_source")
        self.comb += [
            # Control
            udp_source.valid.eq(udp_port.source.valid),
            udp_source.last.eq(udp_port.source.last),
            udp_port.source.ready.eq(udp_source.ready),

            # Param
            udp_source.src_port.eq(udp_port.source.src_port),
            udp_source.dst_port.eq(udp_port.source.dst_port),
            udp_source.ip_address.eq(udp_port.source.ip_address),
            udp_source.length.eq(udp_port.source.length),

            # Payload
            udp_source.data.eq(udp_port.source.data),
            udp_source.error.eq(udp_port.source.error)
        ]

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth standalone core generator")
    builder_args(parser)
    soc_core_args(parser)
    parser.add_argument("--phy", default="mii", help="Ethernet PHY(mii/rmii/gmii/rgmii)")
    parser.add_argument("--core", default="wishbone", help="Ethernet Core(wishbone/udp)")
    parser.add_argument("--endianness", default="big", choices=("big", "little"), help="Wishbone endianness")
    parser.add_argument("--mac_address", default=0x10e2d5000000, help="MAC address")
    parser.add_argument("--ip_address", default="192.168.1.50", help="IP address")
    args = parser.parse_args()

    if args.core == "wishbone":
        soc = MACCore(phy=args.phy, clk_freq=int(100e6), endianness=args.endianness)
    elif args.core == "udp":
        soc =  UDPCore(phy=args.phy, clk_freq=int(100e6),
                       mac_address = args.mac_address,
                       ip_address  = args.ip_address,
                       port        = 6000)
    else:
        raise ValueError
    builder = Builder(soc, output_dir="build", compile_gateware=False, csr_csv="build/csr.csv")
    builder.build(build_name="liteeth_core")

if __name__ == "__main__":
    main()

