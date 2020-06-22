#!/usr/bin/env python3

# This file is Copyright (c) 2019 Yehowshua Immanuel <yimmanuel3@gatech.edu>
# This file is Copyright (c) 2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import sys

from migen import *

from litex.build.generic_platform import *

from litex.boards.platforms import versa_ecp5

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteeth.common import *
from liteeth.phy.ecp5rgmii import LiteEthPHYRGMII
from liteeth.core import LiteEthUDPIPCore

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys = ClockDomain()

        # # #

        self.cd_sys.clk.attr.add("keep")

        # clk / rst
        clk100 = platform.request("clk100")
        platform.add_period_constraint(clk100, 1e9/100e6)

        # pll
        self.submodules.pll = pll = ECP5PLL()
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)


# UDPLoopback ------------------------------------------------------------------------------------------

class UDPLoopback(SoCMini):
    def __init__(self, platform):

        sys_clk_freq = int(150e6)
        SoCMini.__init__(self, platform, sys_clk_freq, ident="UDPLoopback", ident_version=True)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # Ethernet ---------------------------------------------------------------------------------
        # phy
        self.submodules.eth_phy = LiteEthPHYRGMII(
            clock_pads = platform.request("eth_clocks"),
            pads       = platform.request("eth"))
        self.add_csr("eth_phy")
        # core
        self.submodules.eth_core = LiteEthUDPIPCore(
            phy         = self.eth_phy,
            mac_address = 0x10e2d5000000,
            ip_address  = "192.168.1.50",
            clk_freq    = sys_clk_freq)

        # add udp loopback on port 6000 with dw=8
        self.add_udp_loopback(6000, 8,  128, "loopback_8")
        # add udp loopback on port 8000 with dw=32
        self.add_udp_loopback(8000, 32, 128, "loopback_32")

        # timing constraints
        self.eth_phy.crg.cd_eth_rx.clk.attr.add("keep")
        self.eth_phy.crg.cd_eth_tx.clk.attr.add("keep")
        self.platform.add_period_constraint(self.eth_phy.crg.cd_eth_rx.clk, 1e9/125e6)
        self.platform.add_period_constraint(self.eth_phy.crg.cd_eth_tx.clk, 1e9/125e6)

    def add_udp_loopback(self, port, dw, depth, name=None):
        port = self.eth_core.udp.crossbar.get_port(port, dw)
        buf = stream.SyncFIFO(eth_udp_user_description(dw), depth//(dw//8))
        if name is None:
            self.submodules += buf
        else:
            setattr(self.submodules, name, buf)
        self.comb += port.source.connect(buf.sink)
        self.comb += buf.source.connect(port.sink)

# Load ---------------------------------------------------------------------------------------------
def load():
    import os
    f = open("ecp5-versa5g.cfg", "w")
    f.write(
"""
interface ftdi
ftdi_vid_pid 0x0403 0x6010
ftdi_channel 0
ftdi_layout_init 0xfff8 0xfffb
reset_config none
adapter_khz 25000
jtag newtap ecp5 tap -irlen 8 -expected-id 0x81112043
""")
    f.close()
    os.system("openocd -f ecp5-versa5g.cfg -c \"transport select jtag; init; svf build/gateware/top.svf; exit\"")

# Build --------------------------------------------------------------------------------------------

def main():
    if "load" in sys.argv[1:]:
        load()
        exit()
    else:
        platform = versa_ecp5.Platform(toolchain="trellis")
        soc      = UDPLoopback(platform)
        builder  = Builder(soc, output_dir="build", csr_csv="tools/csr.csv")
        vns      = builder.build()

if __name__ == "__main__":
    main()
