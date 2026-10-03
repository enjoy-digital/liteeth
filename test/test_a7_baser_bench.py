#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import tempfile
import unittest
from pathlib import Path

from litex.soc.integration.builder import Builder

from bench import ac701_5g, acorn_baseboard_mini_5g


class TestA7BASERBench(unittest.TestCase):
    def test_boards_generate(self):
        for bench, fbdiv in [(ac701_5g, 5), (acorn_baseboard_mini_5g, 3)]:
            with self.subTest(board=bench.__name__), tempfile.TemporaryDirectory() as path:
                soc = bench.BenchSoC()
                self.assertTrue(soc.ethcore_etherbone.mac.core.with_store_and_forward)
                builder = Builder(soc, output_dir=path, compile_software=False,
                    csr_csv=str(Path(path)/"csr.csv"))
                builder.build(run=False)
                rtl = (Path(builder.gateware_dir)/(soc.build_name + ".v")).read_text()
                self.assertIn("GTPE2_CHANNEL", rtl)
                self.assertRegex(rtl, rf"\.PLL0_FBDIV\s*\(\d+'d{fbdiv}\)")
                # Confirm standard PHY diagnostics survived SoC CSR collection.
                csrs = (Path(path)/"csr.csv").read_text()
                for name in ["ethphy_reset", "ethphy_control", "ethphy_status", "ethphy_rx_prbs_errors"]:
                    self.assertIn("csr_register," + name + ",", csrs)
                config = (Path(builder.generated_dir)/"soc.h").read_text()
                self.assertIn('#define CONFIG_SFP_0_I2C "sfp_i2c"', config)
                self.assertIn('#define CONFIG_SFP_0_HOST_MODE "5GBASER"', config)
                self.assertNotIn("SFP_ROLLBALL_MACTYPE", config)
                if bench is ac701_5g:
                    self.assertIn("#define CONFIG_SFP_0_MUX_ADDR 116", config)
                    self.assertIn("#define CONFIG_SFP_0_MUX_CHANNEL 4", config)
                    self.assertRegex(rtl, r"assign sfp_tx_disable_n\s*=\s*1'd0;")
                xdc = (Path(builder.gateware_dir)/(soc.build_name + ".xdc")).read_text()
                self.assertEqual(xdc.count("-period 3.103"), 2)
                self.assertIn("-period 50.0", xdc)
                self.assertNotIn("set_multicycle_path", xdc)

    def test_ac701_clock_dividers(self):
        registers = dict(ac701_5g.SI5324_INIT)

        def divider(address):
            return ((registers[address] << 16) | (registers[address + 1] << 8) |
                registers[address + 2]) + 1

        n1 = ((registers[25] >> 5) + 4)*divider(31)
        n2 = ((registers[40] >> 5) + 4)*divider(40)
        n31 = divider(43)
        self.assertEqual(156.25e6*n2/n31/n1, ac701_5g.REFCLK_FREQ)
        self.assertEqual(ac701_5g.REFCLK_FREQ*5*5*2, 5.15625e9)
        self.assertEqual(acorn_baseboard_mini_5g.REFCLK_FREQ*3*5*2, 5.15625e9)
