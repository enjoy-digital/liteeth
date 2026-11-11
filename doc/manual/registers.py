#
# This file is part of LiteEth project.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Register appendix of the manual: which designs tools/csr_doc.py elaborates and which register banks
# it documents (the engine is generic, part of the manual kit).

import importlib.util
import json
import os
import sys
import tempfile

# Configurations: script (path from the repository root) and command line, or "manual" and the
# name of a function of this file building the SoC. The generator example gives the software MAC
# and the MII PHY with MDIO; two bench targets give the ECP5 RGMII (Colorlight 5A-75B) and the 10BASE-T PHYs. The
# standalone UDP cores of the generator build their PHYs without registers, so the BASE-R, BASE-X
# and PTP banks come from two small SoCs defined below, elaborated only for this appendix: a
# 10GBASE-R PHY with a hybrid MAC and the PTP core on the XCKU3P board, and a 1000BASE-X PHY with
# the PCS registers enabled on the Acorn Baseboard Mini.
CONFIGS = {
    "wishbone_mii"   : ("liteeth/gen.py",        ["examples/wishbone_mii.yml"]),
    "colorlight"     : ("bench/colorlight_5a_75b.py", []),
    "arty_10baset"   : ("bench/arty_10baset.py", []),
    "xcku3p_10g_ptp" : ("manual", "soc_xcku3p_10g_ptp"),
    "acorn_1000basex": ("manual", "soc_acorn_1000basex"),
}

# Banks documented in the appendix (prefix match on the bank name), in order, with their title and
# the Python class that defines them. The longest matching prefix owns a bank.
BANKS = [
    ("ethmac",            "MAC",            "LiteEthMAC",          "liteeth/mac/__init__.py"),
    ("ethphy_mii",        "MII PHY",        "LiteEthPHYMII",       "liteeth/phy/parallel/mii.py"),
    ("ethphy_ecp5_rgmii", "ECP5 RGMII PHY", "LiteEthECP5PHYRGMII", "liteeth/phy/parallel/rgmii/ecp5.py"),
    ("ethphy_10baset",    "10BASE-T PHY",   "LiteEthPHY10BASET",   "liteeth/phy/serial/baset.py"),
    ("ethphy_basex",      "1000BASE-X PHY", "A7_1000BASEX",        "liteeth/phy/serial/basex/wrappers/a7_gtp.py"),
    ("ethphy_baser",      "BASE-R PHY",     "LiteEthBASERPHY",     "liteeth/phy/serial/baser/wrappers/diagnostics.py"),
    ("ptp",               "PTP",            "LiteEthPTP",          "liteeth/core/ptp/__init__.py"),
]

# Notes printed after the title of a bank: what the register descriptions do not say. Every PHY
# names its bank `ethphy` in a build; the appendix lists it under one name per PHY to keep them
# apart (see PHY_RENAMES below).
NOTES = {
    "ethmac": [
        "The bank of the MAC with SRAM slots (`interface=\"wishbone\"` or `\"hybrid\"`), as",
        "`add_ethernet`, `add_etherbone(with_ethmac=True)` and the `wishbone` and `axi-lite`",
        "flavours of the generator build it. The `sram_writer_` registers describe the receive",
        "slots and the `sram_reader_` registers the transmit slots; their events drive the",
        "`ethmac` interrupt. The slots are memory in the `ethmac` bus region, not registers.",
        "`rx_datapath_drops` exists when the MAC stores whole frames before forwarding them, as",
        "the 64-bit MAC of `xcku3p_10g_ptp` does. `add_ethernet(with_timestamp=True)` adds",
        "`sram_writer_timestamp`, `sram_reader_timestamp_slot` and `sram_reader_timestamp`.",
    ],
    "ethphy_mii": [
        "`crg_reset` holds the PHY, and the external PHY through its reset pin, in reset while",
        "set. `mdio_w` and `mdio_r` drive the MDIO bus bit by bit; the BIOS commands",
        "`mdio_read`, `mdio_write` and `mdio_dump` use them. The parallel PHYs whose pads have",
        "an `mdc` signal have the same MDIO registers, as the ECP5 RGMII PHY shows.",
    ],
    "ethphy_ecp5_rgmii": [
        "`rx_inband_status` decodes the in-band status that the external PHY sends between",
        "frames: link, speed and duplex as the external PHY negotiated them.",
    ],
    "ethphy_10baset": [
        "`status` reports the link, detected from the frames and link pulses of the partner,",
        "and whether the receive pair is wired inverted.",
    ],
    "ethphy_basex": [
        "`reset` resets the transceiver and the PCS. `pcs_status` exists only when the PCS is",
        "built with its registers, as here (`pcs_kwargs` with `with_csr`); the generator and the",
        "bench targets build the BASE-X PHYs without it. The PCS also creates a link event,",
        "which this wrapper does not export as registers.",
    ],
    "ethphy_baser": [
        "The same bank on the Artix-7, Kintex-7 and UltraScale+ BASE-R PHYs built with",
        "`with_csr=True`. The GW5 10GBASE-R PHY adds `serdes_status`, ignores the `loopback`",
        "field and has `rx_prbs_errors` only with `with_prbs=True`. Reading `status` clears its",
        "sticky bits. [Debugging](#debugging) gives the read sequences.",
    ],
    "ptp": [
        "Two status registers only: the enable, mode, clock identity, time and servo state of",
        "the PTP core are gateware signals for the user logic.",
    ],
}

# Opening paragraph of the appendix and the sentence that ends the access paragraph.
INTRO = ["This appendix lists the registers of LiteEth: MAC, PHYs and PTP core. It is generated",
         "from the register definitions of the gateware, elaborated on five configurations",
         "without running an FPGA toolchain. The two `registers.py` configurations are designs",
         "of the manual only, elaborated for this appendix: their clocks are placeholders and",
         "they are not designs to build. Offsets are in bytes from the base of the bank; the",
         "base addresses depend on the SoC and are in the `csr.csv` of a build. Every PHY bank is",
         "named `ethphy` in a build; this appendix suffixes it with the PHY (`ethphy_basex`,",
         "`ethphy_baser`) to keep the PHYs apart. The core layers (ARP, IP, ICMP, UDP), the",
         "Etherbone and the UDP streamer have no registers."]
USAGE = ["The BIOS, Linux and the host tools use these", "names through the generated `csr.h` and `csr.csv`."]
MAIN_BANK = "Registers of the top-level `main` bank of the SoC."

# Every PHY names its bank `ethphy`: the bank is renamed after the PHY of the configuration so that
# each PHY gets its own table.
PHY_RENAMES = {
    "wishbone_mii"    : "ethphy_mii",
    "colorlight"      : "ethphy_ecp5_rgmii",
    "arty_10baset"    : "ethphy_10baset",
    "xcku3p_10g_ptp"  : "ethphy_baser",
    "acorn_1000basex" : "ethphy_basex",
}

# Manual-only designs ------------------------------------------------------------------------------

def _crg(platform):
    """sys clock from the default clock of the board: enough to elaborate, never built."""
    from litex.build.io import CRG
    return CRG(platform.request(platform.default_clk_name))


def soc_xcku3p_10g_ptp():
    """10GBASE-R PHY, 64-bit Etherbone core with a hybrid MAC and the PTP core (XCKU3P board)."""
    from litex.soc.integration.soc_core import SoCMini
    from litex_boards.platforms import alibaba_xcku3p
    from liteeth.phy.serial.baser.wrappers.usp_gt import USP_GTY_10G_BASER
    from liteeth.core.ptp import LiteEthPTP

    sys_clk_freq = int(156.25e6)
    platform     = alibaba_xcku3p.Platform()
    soc          = SoCMini(platform, clk_freq=sys_clk_freq, ident="LiteEth registers")
    soc.crg      = _crg(platform)
    soc.ethphy   = USP_GTY_10G_BASER(
        refclk_or_clk_pads = platform.request("sfp_mgt_clk", 0),
        data_pads          = platform.request("sfp", 0),
        sys_clk_freq       = sys_clk_freq,
        refclk_freq        = 156.25e6,
        with_csr           = True,
    )
    soc.add_etherbone(phy=soc.ethphy, data_width=64, with_ethmac=True)
    udp = soc.ethcore_etherbone.udp
    soc.ptp = LiteEthPTP(
        udp.crossbar.get_port(319, dw=8, cd="sys"),
        udp.crossbar.get_port(320, dw=8, cd="sys"),
        sys_clk_freq,
    )
    return soc


def soc_acorn_1000basex():
    """1000BASE-X PHY on Artix-7 GTP with the PCS registers, 8-bit Etherbone core (Acorn Baseboard Mini)."""
    from litex.gen import Open
    from litex.soc.integration.soc_core import SoCMini
    from litex_boards.platforms import sqrl_acorn
    from liteeth.phy.serial.gtp_7series import QPLLSettings, QPLL
    from liteeth.phy.serial.basex.wrappers.a7_gtp import A7_1000BASEX

    sys_clk_freq = int(125e6)
    platform     = sqrl_acorn.Platform()
    platform.add_extension(sqrl_acorn._litex_acorn_baseboard_mini_io, prepend=True)
    soc          = SoCMini(platform, clk_freq=sys_clk_freq, ident="LiteEth registers")
    soc.crg      = _crg(platform)
    soc.qpll     = QPLL(
        gtgrefclk0    = soc.crg.cd_sys.clk,
        qpllsettings0 = QPLLSettings(refclksel=0b111, fbdiv=4, fbdiv_45=4, refclk_div=1),
        gtgrefclk1    = Open(),
        qpllsettings1 = None,
    )
    soc.ethphy = A7_1000BASEX(
        qpll_channel = soc.qpll.channels[0],
        data_pads    = platform.request("sfp", 0),
        sys_clk_freq = sys_clk_freq,
        pcs_kwargs   = {"with_csr": True, "sys_clk_freq": sys_clk_freq},
    )
    soc.add_etherbone(phy=soc.ethphy)
    return soc

# Elaboration --------------------------------------------------------------------------------------

def _rename(soc, csr_json, old, new):
    soc.csr.regions[new] = soc.csr.regions.pop(old)
    csr_json["csr_bases"][new] = csr_json["csr_bases"][old]
    for reg in list(csr_json["csr_registers"]):
        if reg.startswith(old + "_"):
            csr_json["csr_registers"][new + reg[len(old):]] = csr_json["csr_registers"][reg]


def _load(repo_root, script):
    """Import a script of the repository under a unique module name."""
    name = "manual_" + script.replace("/", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, os.path.join(repo_root, script))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def elaborate(name, repo_root):
    """Run the main() of the generator or of a bench target with its Builder replaced by one that
    elaborates without the toolchain, or build a design of this file the same way; return (soc,
    csr_json, module, argv)."""
    script, argv = CONFIGS[name]
    sys.path.insert(0, repo_root)
    from litex.soc.integration.builder import Builder
    captured = {}
    old_cwd  = os.getcwd()

    with tempfile.TemporaryDirectory() as tmp:
        class CaptureBuilder(Builder):
            def __init__(self, soc, *args, **kwargs):
                kwargs.update(output_dir=tmp, csr_csv=os.path.join(tmp, "csr.csv"),
                    csr_json=os.path.join(tmp, "csr.json"), compile_software=False,
                    compile_gateware=False)
                Builder.__init__(self, soc, **kwargs)
                captured["soc"] = soc

            def build(self, *args, **kwargs):
                kwargs["run"] = False
                return Builder.build(self, *args, **kwargs)

        os.chdir(repo_root)
        old_argv = sys.argv
        try:
            if script == "manual":
                CaptureBuilder(globals()[argv]()).build()
            else:
                sys.path.insert(0, os.path.join(repo_root, os.path.dirname(script)))
                mod = _load(repo_root, script)
                mod.Builder = CaptureBuilder
                sys.argv = [script] + list(argv)
                mod.main()
        finally:
            sys.argv = old_argv
            os.chdir(old_cwd)
        captured["json"] = json.load(open(os.path.join(tmp, "csr.json")))

    # Memories mapped on the CSR bus have no register definitions: keep the register banks only.
    soc, csr_json = captured["soc"], captured["json"]
    for bank, region in list(soc.csr.regions.items()):
        if not isinstance(region.obj, list):
            del soc.csr.regions[bank]
    _rename(soc, csr_json, "ethphy", PHY_RENAMES[name])
    if script == "manual":
        return soc, csr_json, "doc/manual/registers.py", [argv]
    module = os.path.splitext(os.path.basename(script))[0]
    if script.startswith("bench/"):
        module = "bench/" + module
    return soc, csr_json, module, list(argv)
