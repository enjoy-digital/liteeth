#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import sys
import shutil
import unittest
import subprocess
from pathlib import Path

# Helper -------------------------------------------------------------------------------------------

_repo_root    = Path(__file__).resolve().parents[1]
_examples_dir = _repo_root / "examples"
_build_dir    = _examples_dir / "build"

def build_config(name):
    shutil.rmtree(_build_dir, ignore_errors=True)
    try:
        subprocess.run(
            [sys.executable, str(_repo_root / "liteeth" / "gen.py"), f"{name}.yml"],
            cwd   = _examples_dir,
            check = True,
        )
        return int(not (_build_dir / "gateware" / "liteeth_core.v").is_file())
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
