#!/usr/bin/env python3

import argparse

from litex.gen import *

from litex.build.generic_platform import *
from litex.build.xilinx.platform import XilinxPlatform

from litex.soc.interconnect import wishbone
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.phy.mii import LiteEthPHYMII
from liteeth.core.mac import LiteEthMAC

_io = [
    ("sys_clock", 0, Pins(1)),
    ("sys_reset", 1, Pins(1)),

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

    ("eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1)),
    ),
    ("eth", 0,
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
    )
]

class CorePlatform(XilinxPlatform):
    name = "core"
    def __init__(self):
        XilinxPlatform.__init__(self, "xc7", _io)

    def do_finalize(self, *args, **kwargs):
        pass

class WishboneBridge(Module):
    def __init__(self, interface):
        self.wishbone = interface

class Core(SoCCore):
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

    def __init__(self, clk_freq=100*1000000):
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
        self.submodules.ethphy = LiteEthPHYMII(platform.request("eth_clocks"),
                                               platform.request("eth"))
        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone")
        self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"] | self.shadow_base, 0x2000)

        # wishbone
        self.add_cpu_or_bridge(WishboneBridge(platform.request("wishbone")))
        self.add_wb_master(self.cpu_or_bridge.wishbone)


def main():
    parser = argparse.ArgumentParser(description="LiteEth core builder")
    builder_args(parser)
    soc_core_args(parser)
    args = parser.parse_args()

    soc = Core(**soc_core_argdict(args))
    builder = Builder(soc, output_dir="liteeth", compile_gateware=False, csr_csv="liteeth/csr.csv")
    builder.build(build_name="liteeth")

if __name__ == "__main__":
    main()

