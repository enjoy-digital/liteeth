#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import shutil
import tempfile
import unittest

from bench.mac_100g import generate, simulate


@unittest.skipUnless(shutil.which("verilator"), "Verilator is required")
class TestMAC100G(unittest.TestCase):
    def run_traffic(self, lengths, **kwargs):
        with tempfile.TemporaryDirectory() as path:
            return simulate(generate(path, lengths, **kwargs))

    def test_standard_frames(self):
        result = self.run_traffic([1472]*32)
        self.assertEqual(result["rx_dropped"], 0)
        self.assertGreater(result["tx_wire_gbps"], 90)

    def test_minimum_frames_overload(self):
        result = self.run_traffic([18]*128)
        self.assertGreater(result["rx_dropped"], 0)
        self.assertEqual(result["rx_bad"], 0)

    def test_errors_stalls_and_reset(self):
        result = self.run_traffic([18]*64, stall_cycles=200, error_every=13, with_reset=True)
        self.assertEqual(result["rx_bad"], 4)
        self.assertGreaterEqual(result["rx_dropped"], 4)

    def test_mixed_lengths_and_clocks(self):
        result = self.run_traffic([18, 65, 1472, 8972, 33]*3, dw=256,
            sys_clk_freq=200e6, rx_clk_freq=322.26e6, tx_clk_freq=322.27e6,
            rate=25e9, stall_cycles=20)
        self.assertEqual(result["rx_dropped"], 0)
