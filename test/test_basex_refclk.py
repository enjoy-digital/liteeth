#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal

from liteeth.phy.serial.basex.wrappers.ku_gth import KU_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gth import USP_GTH_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gty import USP_GTY_2500BASEX


class TestBASEXReferenceClock(unittest.TestCase):
    def test_ultrascale_2500_defaults_and_options(self):
        for cls, primitive in (
            (KU_2500BASEX, "GTHE3_CHANNEL"),
            (USP_GTH_2500BASEX, "GTHE4_CHANNEL"),
            (USP_GTY_2500BASEX, "GTYE4_CHANNEL"),
        ):
            for fabric in ((False,) if cls is KU_2500BASEX else (False, True)):
                with self.subTest(phy=cls.__name__, fabric=fabric):
                    pads = Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])
                    kwargs = {} if cls is KU_2500BASEX else {"refclk_from_fabric": fabric}
                    phy = cls(Signal(), pads, 100e6, with_csr=False,
                        tx_polarity=1, rx_polarity=1, **kwargs)
                    fragment = phy.get_fragment()
                    self.assertEqual(phy.pll.config["clkin"], 156.25e6)
                    self.assertEqual(phy.pll.config["linerate"], 3.125e9)
                    channel = next(s for s in fragment.specials if isinstance(s, Instance) and s.of == primitive)
                    self.assertEqual(channel.get_io("TXPOLARITY").value, 1)
                    self.assertEqual(channel.get_io("RXPOLARITY").value, 1)
                    self.assertEqual(channel.get_io("CPLLREFCLKSEL").value, 0b111 if fabric else 0b001)

    def test_unsupported_200mhz_2500_reference(self):
        for cls in (KU_2500BASEX, USP_GTH_2500BASEX, USP_GTY_2500BASEX):
            with self.subTest(phy=cls.__name__):
                pads = Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])
                with self.assertRaisesRegex(ValueError, "No config found"):
                    cls(Signal(), pads, 100e6, refclk_freq=200e6, with_csr=False)


if __name__ == "__main__":
    unittest.main()
