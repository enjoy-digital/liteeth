#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Xiretza <xiretza@xiretza.xyz>
# Copyright (c) 2020 Stefan Schrijvers <ximin@ximinity.net>
# Copyright (c) 2022 Victor Suarez Rovere <suarezvictor@gmail.com>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V. <goemansrowan@gmail.com>
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

"""

import argparse
import os
import yaml

from migen import *

from litex.gen import *

from litex.build.generic_platform import *
from litex.build.xilinx.platform import XilinxPlatform
from litex.build.lattice.platform import LatticePlatform

from litex.soc.interconnect import wishbone
from litex.soc.interconnect import axi
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.integration.soc import SoCRegion

from liteeth.common import *

from liteeth import phy as liteeth_phys
from liteeth.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore
from liteeth.core.dhcp import LiteEthDHCP

from liteeth.frontend.stream import LiteEthUDPStreamer
from liteeth.frontend.etherbone import LiteEthEtherbone

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

    # DHCP.
    ("dhcp", 0,
        Subsignal("start",      Pins(1)),
        Subsignal("done",       Pins(1)),
        Subsignal("timeout",    Pins(1)),
        Subsignal("ip_address", Pins(32)),
    ),

    # MII PHY Pads
    ("mii_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1)),
    ),
    ("mii", 0,
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
    ("rmii_clocks", 0,
        Subsignal("ref_clk", Pins(1))
    ),
    ("rmii", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("rx_data", Pins(2)),
        Subsignal("crs_dv",  Pins(1)),
        Subsignal("tx_en",   Pins(1)),
        Subsignal("tx_data", Pins(2)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("mdio",    Pins(1)),
    ),

    # GMII PHY Pads
    ("gmii_clocks", 0,
        Subsignal("tx",  Pins(1)),
        Subsignal("gtx", Pins(1)),
        Subsignal("rx",  Pins(1))
    ),
    ("gmii", 0,
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
    ("rgmii_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1))
    ),
    ("rgmii", 0,
        Subsignal("rst_n",   Pins(1)),
        Subsignal("int_n",   Pins(1)),
        Subsignal("mdio",    Pins(1)),
        Subsignal("mdc",     Pins(1)),
        Subsignal("rx_ctl",  Pins(1)),
        Subsignal("rx_data", Pins(4)),
        Subsignal("tx_ctl",  Pins(1)),
        Subsignal("tx_data", Pins(4))
    ),

    # SGMII PHY Pads
    ("sgmii", 0,
        Subsignal("refclk",  Pins(1)),
        Subsignal("rst",     Pins(1)),
        Subsignal("txp",     Pins(1)),
        Subsignal("txn",     Pins(1)),
        Subsignal("rxp",     Pins(1)),
        Subsignal("rxn",     Pins(1)),
        Subsignal("link_up", Pins(1)),
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
            Subsignal("source_error", Pins(1)),
        ),
    ]

def get_udp_raw_port_ios(name, data_width):
    return [
        (f"{name}", 0,

            # Sink.
            Subsignal("sink_ip_address", Pins(32)),
            Subsignal("sink_src_port",   Pins(16)),
            Subsignal("sink_dst_port",   Pins(16)),
            Subsignal("sink_valid",      Pins(1)),
            Subsignal("sink_length",     Pins(16)),
            Subsignal("sink_last",       Pins(1)),
            Subsignal("sink_ready",      Pins(1)),
            Subsignal("sink_data",       Pins(data_width)),
            Subsignal("sink_last_be",    Pins(data_width//8)),

            # Source.
            Subsignal("source_ip_address", Pins(32)),
            Subsignal("source_src_port",   Pins(16)),
            Subsignal("source_dst_port",   Pins(16)),
            Subsignal("source_valid",      Pins(1)),
            Subsignal("source_length",     Pins(16)),
            Subsignal("source_last",       Pins(1)),
            Subsignal("source_ready",      Pins(1)),
            Subsignal("source_data",       Pins(data_width)),
            Subsignal("source_last_be",    Pins(data_width//8)),
            Subsignal("source_error",      Pins(1)),
        ),
    ]


# PHY Core -----------------------------------------------------------------------------------------

class PHYCore(SoCMini):
    SoCMini.csr_map = {
        "ctrl"   : 0,
        "ethphy" : 1,
        "ethmac" : 2,
    }
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
        self.crg = CRG(platform.request("sys_clock"), platform.request("sys_reset"))

        # PHY --------------------------------------------------------------------------------------
        phy = core_config["phy"]
        # MII.
        if phy in [liteeth_phys.LiteEthPHYMII]:
            ethphy = phy(
                clock_pads = platform.request("mii_clocks"),
                pads       = platform.request("mii"))
        # RMII.
        elif phy in [liteeth_phys.LiteEthPHYRMII]:
            ethphy = phy(
                refclk_cd  = None,
                clock_pads = platform.request("rmii_clocks"),
                pads       = platform.request("rmii"))
        # GMII.
        elif phy in [liteeth_phys.LiteEthPHYGMII]:
            ethphy = phy(
                clock_pads = platform.request("gmii_clocks"),
                pads       = platform.request("gmii"))
        # GMII / MII.
        elif phy in [liteeth_phys.LiteEthPHYGMIIMII]:
            ethphy = phy(
                clock_pads = platform.request("gmii_clocks"),
                pads       = platform.request("gmii"),
                clk_freq   = self.clk_freq)
        # RGMII.
        elif phy in [
            liteeth_phys.LiteEthS7PHYRGMII,
            liteeth_phys.LiteEthECP5PHYRGMII,
        ]:
            ethphy = phy(
                clock_pads         = platform.request("rgmii_clocks"),
                pads               = platform.request("rgmii"),
                tx_delay           = core_config.get("phy_tx_delay", 2e-9),
                rx_delay           = core_config.get("phy_rx_delay", 2e-9),
                with_hw_init_reset = False) # FIXME: required since sys_clk = eth_rx_clk.
        # SGMII.
        elif phy in [
            liteeth_phys.A7_1000BASEX,
            liteeth_phys.A7_2500BASEX,
            liteeth_phys.K7_1000BASEX,
            liteeth_phys.K7_2500BASEX,
            liteeth_phys.KU_1000BASEX,
            liteeth_phys.KU_2500BASEX,
            liteeth_phys.USP_GTH_1000BASEX,
            liteeth_phys.USP_GTH_2500BASEX,
            liteeth_phys.USP_GTY_1000BASEX,
            liteeth_phys.USP_GTY_2500BASEX,
        ]:
            ethphy_pads = platform.request("sgmii")
            # Artix7.
            if phy in [liteeth_phys.A7_1000BASEX, liteeth_phys.A7_2500BASEX]:
                refclk_freq = core_config.get("refclk_freq", 0)
                assert refclk_freq in [125e6, 156.25e6]
                from liteeth.phy.a7_gtp import QPLLSettings, QPLL
                qpll_settings = QPLLSettings(
                    refclksel  = 0b001,
                    fbdiv      = 4,
                    fbdiv_45   = {125e6:5, 156.25e6:4}[refclk_freq],
                    refclk_div = 1
                )
                qpll = QPLL(ethphy_pads.refclk, qpll_settings)
                self.submodules += qpll
                ethphy = phy(
                    qpll_channel = qpll.channels[0],
                    data_pads    = ethphy_pads,
                    sys_clk_freq = self.clk_freq,
                    with_csr     = False,
                    rx_polarity  = core_config.get("phy_rx_polarity", 0),
                    tx_polarity  = core_config.get("phy_tx_polarity", 0),
                )
            # Other 7-Series/Ultrascale(+).
            else:
                ethphy = phy(
                    refclk_or_clk_pads = ethphy_pads.refclk,
                    data_pads          = ethphy_pads,
                    sys_clk_freq       = self.clk_freq,
                    refclk_freq        = core_config.get("refclk_freq", 200e6),
                    with_csr           = False,
                    rx_polarity        = core_config.get("phy_rx_polarity", 0),
                    tx_polarity        = core_config.get("phy_tx_polarity", 0),
                )
            self.comb += [
                ethphy.reset.eq(ethphy_pads.rst),
                ethphy_pads.link_up.eq(ethphy.link_up),
            ]
        else:
            raise ValueError("Unsupported PHY")
        self.ethphy = ethphy

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
        nrxslots        = core_config.get("nrxslots", 2)
        ntxslots        = core_config.get("ntxslots", 2)
        bus_standard    = core_config["core"]
        tx_cdc_depth    = core_config.get("tx_cdc_depth", 32)
        tx_cdc_buffered = core_config.get("tx_cdc_buffered", False)
        rx_cdc_depth    = core_config.get("rx_cdc_depth", 32)
        rx_cdc_buffered = core_config.get("rx_cdc_buffered", False)
        assert bus_standard in ["wishbone", "axi-lite"]

        # PHY --------------------------------------------------------------------------------------
        PHYCore.__init__(self, platform, core_config)

        # MAC --------------------------------------------------------------------------------------
        self.ethmac = ethmac = LiteEthMAC(
            phy             = self.ethphy,
            dw              = 32,
            interface       = "wishbone",
            endianness      = core_config["endianness"],
            nrxslots        = nrxslots,
            ntxslots        = ntxslots,
            full_memory_we  = core_config.get("full_memory_we", False),
            tx_cdc_depth    = tx_cdc_depth,
            tx_cdc_buffered = tx_cdc_buffered,
            rx_cdc_depth    = rx_cdc_depth,
            rx_cdc_buffered = rx_cdc_buffered,
        )

        if bus_standard == "wishbone":
          # Wishbone Interface -----------------------------------------------------------------------
          wb_bus = wishbone.Interface()
          platform.add_extension(wb_bus.get_ios("wishbone"))
          self.comb += wb_bus.connect_to_pads(self.platform.request("wishbone"), mode="slave")
          self.bus.add_master(master=wb_bus)

        if bus_standard == "axi-lite":
          # AXI-Lite Interface -----------------------------------------------------------------------
          axil_bus = axi.AXILiteInterface(address_width=32, data_width=32)
          platform.add_extension(axil_bus.get_ios("bus"))
          self.submodules += axi.Wishbone2AXILite(ethmac.bus, axil_bus)
          self.comb += axil_bus.connect_to_pads(self.platform.request("bus"), mode="slave")
          self.bus.add_master(master=axil_bus)

        ethmac_region_size = (nrxslots + ntxslots)*buffer_depth
        ethmac_region = SoCRegion(origin=self.mem_map.get("ethmac", None), size=ethmac_region_size, cached=False)
        self.bus.add_slave(name="ethmac", slave=ethmac.bus, region=ethmac_region)

        # Interrupt Interface ----------------------------------------------------------------------
        self.comb += self.platform.request("interrupt").eq(self.ethmac.ev.irq)

# UDP Core -----------------------------------------------------------------------------------------

class UDPCore(PHYCore):
    def add_streamer_port(self, platform, name, port_cfg):
        # Use default Data-Width of 8-bit when not specified.
        data_width = port_cfg.get("data_width", 8)

        # Used dynamic UDP-Port/IP-Address when not specified.
        dynamic_params = port_cfg.get("ip_address", None) is None

        # FIFO Depth.
        tx_fifo_depth = port_cfg.get("tx_fifo_depth", 64)
        rx_fifo_depth = port_cfg.get("rx_fifo_depth", 64)

        # Create/Add IOs.
        # ---------------
        platform.add_extension(get_udp_port_ios(name,
            data_width     = data_width,
            dynamic_params = dynamic_params
        ))

        port_ios = platform.request(name)

        if dynamic_params:
            ip_address = port_ios.ip_address
            udp_port   = port_ios.udp_port
        else:
            ip_address = port_cfg.get("ip_address")
            udp_port   = port_cfg.get("udp_port")

        # Create UDPStreamer.
        # -------------------
        udp_streamer = LiteEthUDPStreamer(self.core.udp,
            ip_address    = ip_address,
            udp_port      = udp_port,
            data_width    = data_width,
            tx_fifo_depth = tx_fifo_depth,
            rx_fifo_depth = rx_fifo_depth
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
            port_ios.source_data.eq(udp_streamer.source.data),
            port_ios.source_error.eq(udp_streamer.source.error),
        ]

    def add_raw_port(self, platform, name, port_cfg):
        # Use default Data-Width of 8-bit when not specified.
        data_width = port_cfg.get("data_width", 8)

        # Create/Add IOs.
         # ---------------
        platform.add_extension(get_udp_raw_port_ios(name,
            data_width     = data_width,
         ))

        port_ios = platform.request(name)

        raw_port = self.core.udp.crossbar.get_port(port_ios.sink_dst_port, dw=data_width)

        # Connect IOs.
        # ------------
        # Connect UDP Sink IOs to UDP.
        self.comb += [
            raw_port.sink.valid.eq(port_ios.sink_valid),
            raw_port.sink.last.eq(port_ios.sink_last),
            raw_port.sink.dst_port.eq(port_ios.sink_dst_port),
            raw_port.sink.src_port.eq(port_ios.sink_src_port),
            raw_port.sink.ip_address.eq(port_ios.sink_ip_address),
            raw_port.sink.length.eq(port_ios.sink_length),
            port_ios.sink_ready.eq(raw_port.sink.ready),
            raw_port.sink.data.eq(port_ios.sink_data),
            raw_port.sink.last_be.eq(port_ios.sink_last_be),
        ]

        # Connect UDP to UDP Source IOs.
        self.comb += [
            port_ios.source_valid.eq(raw_port.source.valid),
            port_ios.source_last.eq(raw_port.source.last),
            port_ios.source_dst_port.eq(raw_port.source.dst_port),
            port_ios.source_src_port.eq(raw_port.source.src_port),
            port_ios.source_ip_address.eq(raw_port.source.ip_address),
            port_ios.source_length.eq(raw_port.source.length),
            raw_port.source.ready.eq(port_ios.source_ready),
            port_ios.source_data.eq(raw_port.source.data),
            port_ios.source_last_be.eq(raw_port.source.last_be),
            port_ios.source_error.eq(raw_port.source.error),
        ]

    def __init__(self, platform, core_config):
        # Config -----------------------------------------------------------------------------------
        tx_cdc_depth    = core_config.get("tx_cdc_depth", 32)
        tx_cdc_buffered = core_config.get("tx_cdc_buffered", False)
        rx_cdc_depth    = core_config.get("rx_cdc_depth", 32)
        rx_cdc_buffered = core_config.get("rx_cdc_buffered", False)

        # MAC Address.
        mac_address = core_config.get("mac_address", None)
        # Get MAC Address from IOs when not specified.
        if mac_address is None:
            mac_address = platform.request("mac_address")

        # IP Address.
        dhcp       = core_config.get("dhcp", False)
        ip_address = core_config.get("ip_address", None)
        # Get IP Address from IOs when not specified.
        if ip_address is None:
            ip_address = platform.request("ip_address")
        else:
            assert not dhcp

        # PHY --------------------------------------------------------------------------------------
        PHYCore.__init__(self, platform, core_config)

        # Core -------------------------------------------------------------------------------------
        data_width = core_config.get("data_width", 8)
        self.core = LiteEthUDPIPCore(self.ethphy,
            mac_address       = mac_address,
            ip_address        = ip_address,
            clk_freq          = core_config["clk_freq"],
            dw                = data_width,
            with_sys_datapath = (data_width == 32),
            tx_cdc_depth      = tx_cdc_depth,
            tx_cdc_buffered   = tx_cdc_buffered,
            rx_cdc_depth      = rx_cdc_depth,
            rx_cdc_buffered   = rx_cdc_buffered,
        )

        # DHCP -------------------------------------------------------------------------------------

        if dhcp:
            dhcp_pads = platform.request("dhcp")
            dhcp_port = self.core.udp.crossbar.get_port(68, dw=32, cd="sys")
            if isinstance(mac_address, Signal):
                dhcp_mac_address = mac_address
            else:
                dhcp_mac_address = Signal(48, reset=0x10e2d5000001)
            self.dhcp = LiteEthDHCP(udp_port=dhcp_port, sys_clk_freq=self.sys_clk_freq)
            self.comb += [
                self.dhcp.start.eq(dhcp_pads.start),
                dhcp_pads.done.eq(self.dhcp.done),
                dhcp_pads.timeout.eq(self.dhcp.timeout),
                dhcp_pads.ip_address.eq(self.dhcp.ip_address),
            ]

        # Etherbone --------------------------------------------------------------------------------

        etherbone              = core_config.get("etherbone", False)
        etherbone_port         = core_config.get("etherbone_port", 1234)
        etherbone_buffer_depth = core_config.get("etherbone_buffer_depth", 16)

        if etherbone:
            assert (data_width == 32)
            self.etherbone = LiteEthEtherbone(
                udp          =  self.core.udp,
                udp_port     = etherbone_port,
                buffer_depth = etherbone_buffer_depth,
                cd           = "sys"
            )
            axil_bus = axi.AXILiteInterface(address_width=32, data_width=32)
            platform.add_extension(axil_bus.get_ios("mmap"))
            self.submodules += axi.Wishbone2AXILite(self.etherbone.wishbone.bus, axil_bus)
            self.comb += axil_bus.connect_to_pads(platform.request("mmap"), mode="master")

        # UDP Ports --------------------------------------------------------------------------------
        for name, port_cfg in core_config["udp_ports"].items():
            # mode either `raw` or `stream`, default to streamer to be backwards compatible
            mode = port_cfg.get("mode", "streamer")
            assert mode == "raw" or mode == "streamer"

            if mode == "streamer":
                self.add_streamer_port(platform, name, port_cfg)
            elif mode == "raw":
                self.add_raw_port(platform, name, port_cfg)


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
        if k in ["refclk_freq", "clk_freq"]:
            core_config[k] = int(float(core_config[k]))
        if k in ["phy_tx_delay", "phy_rx_delay"]:
            core_config[k] = float(core_config[k])

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

    if core_config["core"] in ["wishbone", "axi-lite"]:
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
