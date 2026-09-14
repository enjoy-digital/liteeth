#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import sys
import shutil
import unittest
import subprocess
import tempfile
from pathlib import Path

import yaml

# Helper -------------------------------------------------------------------------------------------

_repo_root    = Path(__file__).resolve().parents[1]
_examples_dir = _repo_root / "examples"
_build_dir    = _examples_dir / "build"

def generate_config(config_file, output_dir):
    subprocess.run(
        [
            sys.executable,
            str(_repo_root / "liteeth" / "gen.py"),
            str(config_file),
            "--output-dir",
            str(output_dir),
        ],
        cwd   = _examples_dir,
        check = True,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        text   = True,
    )
    return output_dir / "gateware" / "liteeth_core.v"

def generate_example_verilog(name):
    with tempfile.TemporaryDirectory() as tmpdir:
        verilog = generate_config(
            config_file = _examples_dir / f"{name}.yml",
            output_dir  = Path(tmpdir) / "build",
        )
        return verilog.read_text(encoding="utf-8")

def generate_config_dict_verilog(config):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir      = Path(tmpdir)
        config_file = tmpdir / "config.yml"
        with config_file.open("w", encoding="utf-8") as f:
            yaml.safe_dump(config, f)
        verilog = generate_config(
            config_file = config_file,
            output_dir  = tmpdir / "build",
        )
        return verilog.read_text(encoding="utf-8")

def build_config(name):
    shutil.rmtree(_build_dir, ignore_errors=True)
    try:
        verilog = generate_config(
            config_file = _examples_dir / f"{name}.yml",
            output_dir  = _build_dir,
        )
        return int(not verilog.is_file())
    finally:
        shutil.rmtree(_build_dir, ignore_errors=True)

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

    def test_udp_raw_static_listen_port(self):
        verilog = generate_example_verilog("udp_raw_ecp5rgmii")
        self.assertNotIn("raw_udp_listen_port", verilog)
        self.assertRegex(verilog, r"crossbar_sink_param_dst_port == \d+'d2000")
        self.assertNotIn("crossbar_sink_param_dst_port == raw_sink_dst_port", verilog)

    def test_udp_raw_dynamic_listen_port(self):
        with (_examples_dir / "udp_raw_ecp5rgmii.yml").open(encoding="utf-8") as f:
            config = yaml.safe_load(f)
        del config["udp_ports"]["raw"]["udp_port"]

        verilog = generate_config_dict_verilog(config)
        self.assertIn("raw_udp_listen_port", verilog)
        self.assertIn("crossbar_sink_param_dst_port == raw_udp_listen_port", verilog)
        self.assertNotIn("crossbar_sink_param_dst_port == raw_sink_dst_port", verilog)

    def test_udp_raw_rejects_static_ip(self):
        with (_examples_dir / "udp_raw_ecp5rgmii.yml").open(encoding="utf-8") as f:
            config = yaml.safe_load(f)
        config["udp_ports"]["raw"]["ip_address"] = "172.30.0.100"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir      = Path(tmpdir)
            config_file = tmpdir / "config.yml"
            with config_file.open("w", encoding="utf-8") as f:
                yaml.safe_dump(config, f)
            with self.assertRaises(subprocess.CalledProcessError):
                generate_config(
                    config_file = config_file,
                    output_dir  = tmpdir / "build",
                )
