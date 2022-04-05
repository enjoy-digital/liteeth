#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2022 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Xiretza <xiretza@xiretza.xyz>
# Copyright (c) 2020 Stefan Schrijvers <ximin@ximinity.net>
# SPDX-License-Identifier: BSD-2-Clause

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
import os
import yaml

from migen import *

from litex.build.generic_platform import *
from litex.build.xilinx.platform import XilinxPlatform
from litex.build.lattice.platform import LatticePlatform

from litex.soc.interconnect import wishbone
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.integration.soc import SoCRegion

from liteeth.common import *

from liteeth import phy as liteeth_phys
from liteeth.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore

# IOs ----------------------------------------------------------------------------------------------

_io = [
    # Clk / Rst
    ("sys_clock", 0, Pins(1)),
    ("sys_reset", 1, Pins(1)),

    # IP/MAC Address.
    ("mac_address", 0, Pins(48)),
    ("ip_address",  0, Pins(32)),

    # Interrupt
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
]

def get_udp_port_ios(name, data_width, dynamic_params=False):
    return [
        (f"{name}", 0,
            # Parameters.
            *([
                Subsignal("udp_port",   Pins(16)),
                Subsignal("ip_address", Pins(32)),
            ] if dynamic_params else []),

            # Sink.
            Subsignal("sink_valid", Pins(1)),
            Subsignal("sink_last",  Pins(1)),
            Subsignal("sink_ready", Pins(1)),
            Subsignal("sink_data",  Pins(data_width)),

            # Source.
            Subsignal("source_valid", Pins(1)),
            Subsignal("source_last",  Pins(1)),
            Subsignal("source_ready", Pins(1)),
            Subsignal("source_data",  Pins(data_width)),
        ),
    ]

# PHY Core -----------------------------------------------------------------------------------------

class PHYCore(SoCMini):
    def __init__(self, platform, core_config):
        for deprecated in ("csr_map", "mem_map"):
            if deprecated in core_config:
                raise RuntimeWarning("Config option {!r} is now a sub-option of 'soc'".format(deprecated))

        # SoC parameters ---------------------------------------------------------------------------
        soc_args = {}
        if "soc" in core_config:
            soc_config = core_config["soc"]

            for arg in soc_config:
                if arg in ("csr_map", "interrupt_map", "mem_map"):
                    getattr(self, arg).update(soc_config[arg])
                else:
                    soc_args[arg] = soc_config[arg]

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=core_config["clk_freq"], **soc_args)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = CRG(platform.request("sys_clock"), platform.request("sys_reset"))

        # PHY --------------------------------------------------------------------------------------
        phy = core_config["phy"]
        if phy in [liteeth_phys.LiteEthPHYMII]:
            ethphy = phy(
                clock_pads = platform.request("mii_eth_clocks"),
                pads       = platform.request("mii_eth"))
        elif phy in [liteeth_phys.LiteEthPHYRMII]:
            ethphy = phy(
                clock_pads = platform.request("rmii_eth_clocks"),
                pads       = platform.request("rmii_eth"))
        elif phy in [liteeth_phys.LiteEthPHYGMII]:
            ethphy = phy(
                clock_pads = platform.request("gmii_eth_clocks"),
                pads       = platform.request("gmii_eth"))
        elif phy in [liteeth_phys.LiteEthPHYGMIIMII]:
            ethphy = phy(
                clock_pads = platform.request("gmii_eth_clocks"),
                pads       = platform.request("gmii_eth"),
                clk_freq   = self.clk_freq)
        elif phy in [liteeth_phys.LiteEthS7PHYRGMII, liteeth_phys.LiteEthECP5PHYRGMII]:
            ethphy = phy(
                clock_pads         = platform.request("rgmii_eth_clocks"),
                pads               = platform.request("rgmii_eth"),
                tx_delay           = core_config.get("phy_tx_delay", 2e-9),
                rx_delay           = core_config.get("phy_rx_delay", 2e-9),
                with_hw_init_reset = False) # FIXME: required since sys_clk = eth_rx_clk.
        else:
            raise ValueError("Unsupported PHY")
        self.submodules.ethphy = ethphy

        # Timing constaints.
        # Generate timing constraints to ensure the "keep" attribute is properly set on the various
        # clocks. This also adds the constraints to the generated .xdc that can then be "imported"
        # in the project using the core.
        eth_rx_clk = getattr(ethphy, "crg", ethphy).cd_eth_rx.clk
        eth_tx_clk = getattr(ethphy, "crg", ethphy).cd_eth_tx.clk
        from liteeth.phy.model import LiteEthPHYModel
        if not isinstance(ethphy, LiteEthPHYModel):
            self.platform.add_period_constraint(eth_rx_clk, 1e9/phy.rx_clk_freq)
            self.platform.add_period_constraint(eth_tx_clk, 1e9/phy.tx_clk_freq)
            self.platform.add_false_path_constraints(self.crg.cd_sys.clk, eth_rx_clk, eth_tx_clk)

# MAC Core -----------------------------------------------------------------------------------------

class MACCore(PHYCore):
    def __init__(self, platform, core_config):
        # Parameters -------------------------------------------------------------------------------
        nrxslots = core_config.get("nrxslots", 2)
        ntxslots = core_config.get("ntxslots", 2)

        # PHY --------------------------------------------------------------------------------------
        PHYCore.__init__(self, platform, core_config)

        # MAC --------------------------------------------------------------------------------------
        self.submodules.ethmac = ethmac = LiteEthMAC(
            phy            = self.ethphy,
            dw             = 32,
            interface      = "wishbone",
            endianness     = core_config["endianness"],
            nrxslots       = nrxslots,
            ntxslots       = ntxslots,
            full_memory_we = core_config.get("full_memory_we", False))

        # Wishbone Interface -----------------------------------------------------------------------
        ethmac_region_size = (nrxslots + ntxslots)*buffer_depth
        ethmac_region = SoCRegion(origin=self.mem_map.get("ethmac", None), size=ethmac_region_size, cached=False)
        self.bus.add_slave(name="ethmac", slave=ethmac.bus, region=ethmac_region)

        # Interrupt Interface ----------------------------------------------------------------------
        self.comb += self.platform.request("interrupt").eq(self.ethmac.ev.irq)

# UDP Core -----------------------------------------------------------------------------------------

class UDPCore(PHYCore):
    def __init__(self, platform, core_config):
        from liteeth.frontend.stream import LiteEthUDPStreamer

        # Config -----------------------------------------------------------------------------------

        # MAC Address.
        mac_address = core_config.get("mac_address", None)
        # Get MAC Address from IOs when not specified.
        if mac_address is None:
            mac_address = platform.request("mac_address")

        # IP Address.
        ip_address = core_config.get("ip_address", None)
        # Get IP Address from IOs when not specified.
        if ip_address is None:
            ip_address = platform.request("ip_address")

        # PHY --------------------------------------------------------------------------------------
        PHYCore.__init__(self, platform, core_config)

        # Core -------------------------------------------------------------------------------------
        self.submodules.core = LiteEthUDPIPCore(self.ethphy,
            mac_address = mac_address,
            ip_address  = ip_address,
            clk_freq    = core_config["clk_freq"]
        )

        # UDP Ports --------------------------------------------------------------------------------
        for name, port in core_config["udp_ports"].items():
            # Parameters.
            # -----------

            # Use default Data-Width of 8-bit when not specified.
            data_width = port.get("data_width", 8)

            # Used dynamic UDP-Port/IP-Address when not specified.
            dynamic_params = port.get("ip_address", None) is None

            # FIFO Depth.
            tx_fifo_depth = port.get("tx_fifo_depth", 64)
            rx_fifo_depth = port.get("rx_fifo_depth", 64)

            # UDP payloads are data_width * send_level long.
            send_level = port.get("send_level", 1)

            # Create/Add IOs.
            # ---------------
            platform.add_extension(get_udp_port_ios(name,
                data_width     = data_width,
                dynamic_params = dynamic_params
            ))
            port_ios = platform.request(name)

            # Create UDPStreamer.
            # -------------------
            if dynamic_params:
                ip_address = port_ios.ip_address
                udp_port   = port_ios.udp_port
            else:
                ip_address = port.get("ip_address")
                udp_port   = port.get("udp_port")
            udp_streamer = LiteEthUDPStreamer(self.core.udp,
                ip_address    = ip_address,
                udp_port      = udp_port,
                data_width    = data_width,
                tx_fifo_depth = tx_fifo_depth,
                rx_fifo_depth = rx_fifo_depth,
                send_level    = send_level
            )
            self.submodules += udp_streamer

            # Connect IOs.
            # ------------
             # Connect UDP Sink IOs to UDP Steamer.
            self.comb += [
                udp_streamer.sink.valid.eq(port_ios.sink_valid),
                udp_streamer.sink.last.eq(port_ios.sink_last),
                port_ios.sink_ready.eq(udp_streamer.sink.ready),
                udp_streamer.sink.data.eq(port_ios.sink_data)
            ]

            # Connect UDP Streamer to UDP Source IOs.
            self.comb += [
                port_ios.source_valid.eq(udp_streamer.source.valid),
                port_ios.source_last.eq(udp_streamer.source.last),
                udp_streamer.source.ready.eq(port_ios.source_ready),
                port_ios.source_data.eq(udp_streamer.source.data)
            ]

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth standalone core generator")
    builder_args(parser)
    parser.set_defaults(output_dir="build")
    parser.add_argument("config", help="YAML config file")
    args = parser.parse_args()
    core_config = yaml.load(open(args.config).read(), Loader=yaml.Loader)

    # Convert YAML elements to Python/LiteX --------------------------------------------------------
    for k, v in core_config.items():
        replaces = {"False": False, "True": True, "None": None}
        for r in replaces.keys():
            if v == r:
                core_config[k] = replaces[r]
        if k == "phy":
            core_config[k] = getattr(liteeth_phys, core_config[k])
        if k in ["clk_freq", "phy_tx_delay", "phy_rx_delay"]:
            core_config[k] = int(float(core_config[k]))

    # Generate core --------------------------------------------------------------------------------
    if  "device" not in core_config:
        core_config["device"] = ""
    if core_config["vendor"] == "lattice":
        toolchain = core_config.get("toolchain", "diamond")
        platform  = LatticePlatform(core_config["device"], io=[], toolchain=toolchain)
    elif core_config["vendor"] == "xilinx":
        toolchain = core_config.get("toolchain", "vivado")
        platform  = XilinxPlatform(core_config["device"], io=[], toolchain=toolchain)
    else:
        raise ValueError("Unsupported vendor: {}".format(core_config["vendor"]))
    platform.add_extension(_io)

    if core_config["core"] == "wishbone":
        soc = MACCore(platform, core_config)
    elif core_config["core"] == "udp":
        soc = UDPCore(platform, core_config)
    else:
        raise ValueError("Unknown core: {}".format(core_config["core"]))

    builder_arguments = builder_argdict(args)
    builder_arguments["compile_gateware"] = False
    if builder_arguments["csr_csv"] is None:
        builder_arguments["csr_csv"] = os.path.join(builder_arguments["output_dir"], "csr.csv")

    builder = Builder(soc, **builder_arguments)
    builder.build(build_name="liteeth_core")

if __name__ == "__main__":
    main()
