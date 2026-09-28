#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal

from liteeth.phy.serial.basex.wrappers.v7_gth import V7_1000BASEX, V7_2500BASEX


class TestV7BASEX(unittest.TestCase):
    def test_reference_clock_and_rate_configuration(self):
        variants = [
            (V7_1000BASEX, 200e6,    8, 8, 0x03000023ff10080020),
            (V7_1000BASEX, 156.25e6, 4, 7, 0x03000023ff10100020),
            (V7_2500BASEX, 156.25e6, 2, 7, 0x03000023ff10200020),
        ]
        for cls, refclk_freq, divider, clk25_div, rxcdr_cfg in variants:
            for differential in (False, True):
                with self.subTest(phy=cls.__name__, refclk=refclk_freq, differential=differential):
                    pads = Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])
                    refclk = Record([("p", 1), ("n", 1)]) if differential else Signal()
                    phy = cls(refclk, pads, 100e6, refclk_freq=refclk_freq, with_csr=False)
                    fragment = phy.get_fragment()
                    self.assertEqual(phy.pll.config["clkin"], refclk_freq)
                    self.assertEqual(phy.pll.config["linerate"], cls.linerate)
                    self.assertEqual(phy.pll.config["d"], divider)
                    channel = next(s for s in fragment.specials if isinstance(s, Instance) and s.of == "GTHE2_CHANNEL")
                    params = {p.name: p.value.value for p in channel.items
                        if isinstance(p, Instance.Parameter) and hasattr(p.value, "value")}
                    self.assertEqual(params["RXOUT_DIV"], divider)
                    self.assertEqual(params["TXOUT_DIV"], divider)
                    self.assertEqual(params["RX_CLK25_DIV"], clk25_div)
                    self.assertEqual(params["TX_CLK25_DIV"], clk25_div)
                    self.assertEqual(params["RXCDR_CFG"], rxcdr_cfg)

    def test_defaults_and_unsupported_2500_reference(self):
        for cls, refclk_freq in ((V7_1000BASEX, 200e6), (V7_2500BASEX, 156.25e6)):
            with self.subTest(phy=cls.__name__):
                pads = Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])
                phy = cls(Signal(), pads, 100e6)
                phy.get_fragment()
                self.assertEqual(phy.pll.config["clkin"], refclk_freq)
                self.assertEqual(phy._reset.size, 1)
        with self.assertRaisesRegex(ValueError, "Unsupported reference clock"):
            V7_2500BASEX(Signal(), pads, 100e6, refclk_freq=200e6, with_csr=False)


if __name__ == "__main__":
    unittest.main()
