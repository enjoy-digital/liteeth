#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import re
import sys
import copy
import yaml
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

# Helper -------------------------------------------------------------------------------------------

_repo_root    = Path(__file__).resolve().parents[1]
_examples_dir = _repo_root / "examples"
_build_dir    = _examples_dir / "build"

def generator_env():
    env = os.environ.copy()
    paths = [str(_repo_root)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env

def build_config(name):
    shutil.rmtree(_build_dir, ignore_errors=True)
    try:
        subprocess.run(
            [sys.executable, str(_repo_root / "liteeth" / "gen.py"), f"{name}.yml"],
            cwd   = _examples_dir,
            env   = generator_env(),
            check = True,
        )
        return int(not (_build_dir / "gateware" / "liteeth_core.v").is_file())
    finally:
        shutil.rmtree(_build_dir, ignore_errors=True)

def generate_config(name, **overrides):
    """Generate an example config (with optional top-level overrides, None removes a key) in a
    temporary directory and return the generated Verilog."""
    with open(_examples_dir / f"{name}.yml", "r", encoding="utf-8") as f:
        config = yaml.load(f, Loader=yaml.Loader)
    for k, v in overrides.items():
        if v is None:
            config.pop(k, None)
        else:
            config[k] = v
    with tempfile.TemporaryDirectory() as tmp:
        tmp        = Path(tmp)
        config_yml = tmp / f"{name}.yml"
        with open(config_yml, "w", encoding="utf-8") as f:
            yaml.dump(config, f)
        gen_cmd = [sys.executable, str(_repo_root / "liteeth" / "gen.py"), str(config_yml)]
        gen_cmd += ["--output-dir", str(tmp / "build")]
        subprocess.run(gen_cmd, cwd=tmp, env=generator_env(), check=True, stdout=subprocess.DEVNULL)
        with open(tmp / "build" / "gateware" / "liteeth_core.v", "r", encoding="utf-8") as f:
            return f.read()

def strip_comments(verilog):
    return "\n".join(l for l in verilog.split("\n") if not l.lstrip().startswith("//"))

# Test Examples ------------------------------------------------------------------------------------

class TestExamples(unittest.TestCase):
    def test_udp_s7phyrgmii(self):
        errors = build_config("udp_s7phyrgmii")
        self.assertEqual(errors, 0)

    def test_wishbone_mii(self):
        errors = build_config("wishbone_mii")
        self.assertEqual(errors, 0)

    def test_udp_raw_rgmii(self):
        errors = build_config("udp_raw_ecp5rgmii")
        self.assertEqual(errors, 0)

    def test_udp_xgmii_jumbo(self):
        errors = build_config("udp_xgmii_jumbo")
        self.assertEqual(errors, 0)

# Test Generated Core ------------------------------------------------------------------------------

class TestGeneratedCore(unittest.TestCase):
    def test_raw_byte_enable_pins(self):
        for legacy in [False, True]:
            with self.subTest(legacy=legacy):
                ports = {"raw" : {"data_width" : 32, "mode" : "raw", "with_last_be" : legacy}}
                verilog = generate_config("udp_raw_ecp5rgmii", udp_ports=ports)
                field = "last_be" if legacy else "be"
                self.assertIn(f"raw_sink_{field}", verilog)
                self.assertIn(f"raw_source_{field}", verilog)
                if not legacy:
                    self.assertNotIn("raw_sink_last_be", verilog)

    def test_basex_phy_generation(self):
        for phy, primitive in [
            ("K7_1000BASEX", "GTXE2_CHANNEL"),
            ("K7_2500BASEX", "GTXE2_CHANNEL"),
            ("KU_1000BASEX", "GTHE3_CHANNEL"),
            ("KU_2500BASEX", "GTHE3_CHANNEL"),
            ("USP_GTH_2500BASEX", "GTHE4_CHANNEL"),
            ("USP_GTY_2500BASEX", "GTYE4_CHANNEL"),
        ]:
            with self.subTest(phy=phy):
                verilog = generate_config("udp_s7phyrgmii", phy=phy)
                self.assertIn(primitive, verilog)

    def test_basex_explicit_reference_overrides_constructor_default(self):
        for refclk, divider in ((None, 8), (156.25e6, 7)):
            with self.subTest(refclk=refclk):
                verilog = generate_config("udp_s7phyrgmii", phy="KU_1000BASEX", refclk_freq=refclk)
                match = re.search(r"\.TX_CLK25_DIV\s*\(\s*(?:\d+'d)?(\d+)\s*\)", verilog)
                self.assertIsNotNone(match, "Missing TX_CLK25_DIV parameter")
                self.assertEqual(int(match[1]), divider)

    def test_udp_streamer_tkeep_pins(self):
        # tkeep pins are exposed by default and removed with with_tkeep: False.
        verilog = generate_config("udp_xgmii_jumbo")
        self.assertIn("udp0_sink_keep",   verilog)
        self.assertIn("udp0_source_keep", verilog)
        udp_ports = {"udp0": {"data_width": 64, "with_tkeep": False}}
        verilog   = generate_config("udp_xgmii_jumbo", udp_ports=udp_ports)
        self.assertNotIn("udp0_sink_keep",   verilog)
        self.assertNotIn("udp0_source_keep", verilog)

    def test_jumbo_frames_alias(self):
        # jumbo_frames: True is equivalent to eth_mtu: 9022 and differs from the default MTU.
        jumbo   = strip_comments(generate_config("udp_xgmii_jumbo"))
        alias   = strip_comments(generate_config("udp_xgmii_jumbo", jumbo_frames=True, eth_mtu=None))
        default = strip_comments(generate_config("udp_xgmii_jumbo", eth_mtu=None))
        self.assertEqual(jumbo, alias)
        self.assertNotEqual(jumbo, default)

    def test_dynamic_params_not_used_as_reset_values(self):
        # Regression: dynamic udp_port/ip_address pads (port without a fixed IP address) were used
        # as Signal reset values, generating non-constant register initializers that synthesis
        # rejects. No register may be initialized from an input port.
        verilog = generate_config("udp_xgmii_jumbo")
        inputs  = re.findall(r"^\s*input\s+wire\s+(?:\[[^\]]+\]\s+)?(\w+)", verilog, re.M)
        self.assertIn("udp0_udp_port",   inputs)
        self.assertIn("udp0_ip_address", inputs)
        for name in inputs:
            reg_init = rf"(?m)^\s*reg\s+(?:\[[^\]]+\]\s+)?\w+\s*=\s*{name};"
            self.assertNotRegex(verilog, reg_init, msg=name)
