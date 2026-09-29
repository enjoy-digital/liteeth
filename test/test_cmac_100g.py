#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import tempfile
import unittest
from pathlib import Path

from bench.cmac_100g import generate


class TestCMAC100G(unittest.TestCase):
    def test_elaboration(self):
        with tempfile.TemporaryDirectory() as path:
            generate(path)
            rtl = (Path(path)/"cmac_adapter.v").read_text()
            self.assertIn("liteeth_cmac", rtl)
            self.assertRegex(rtl, r"\.tx_axis_tkeep\s*\(")
            self.assertRegex(rtl, r"\.rx_axis_tkeep\s*\(")
            self.assertNotIn(".rx_axis_tready", rtl)
            self.assertRegex(rtl, r"\.rx_clk\s*\(")
            self.assertIn("FDPE", rtl)
