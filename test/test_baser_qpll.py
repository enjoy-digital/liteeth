#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal

from liteiclink.serdes.gth4_ultrascale import GTH4QuadPLL
from liteiclink.serdes.gty_ultrascale import GTYQuadPLL

from liteeth.phy.serial.baser.pma.gty_usp import PMA_USP_GTY_25G_BASER
from liteeth.phy.serial.baser.wrappers.usp_gt import USP_GTY_10G_BASER, USP_GTY_25G_BASER


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


def primitive_parameters(fragment, name):
    instance = next(s for s in fragment.specials if isinstance(s, Instance) and s.of == name)
    return {p.name: p.value.value if hasattr(p.value, "value") else p.value
        for p in instance.items if isinstance(p, Instance.Parameter)}


class TestBASERQPLL(unittest.TestCase):
    def test_25g_integer_and_fractional_parameters(self):
        channels = []
        for refclk, n, fraction in ((156.25e6, 82, 0.5), (161.1328125e6, 80, 0)):
            with self.subTest(refclk=refclk):
                phy = USP_GTY_25G_BASER(Signal(), data_pads(), 100e6,
                    refclk_freq=refclk, with_csr=False)
                self.assertEqual(phy.pll.config["qpll"], "qpll0")
                self.assertEqual(phy.pll.config["f"], fraction)
                self.assertEqual(phy.pll.sdm0_data.reset.value, int(fraction * 2**24))
                fragment = phy.get_fragment()
                params = primitive_parameters(fragment, "GTYE4_COMMON")
                self.assertEqual(params["QPLL0_FBDIV"], n)
                self.assertEqual(params["QPLL0_SDM_CFG0"], 0 if fraction else 0x80)
                self.assertEqual(params["QPLL0CLKOUT_RATE"], "FULL")
                self.assertEqual(params["PPF0_CFG"], 0x800)
                self.assertEqual(params["QPLL0_CFG2"], 0xfc3)
                self.assertEqual(params["QPLL0_CFG2_G3"], 0xfc3)
                self.assertEqual(params["QPLL0_CFG4"], 0x84)
                self.assertEqual(params["QPLL0_LPF"], 0x21f)
                channels.append(primitive_parameters(fragment, "GTYE4_CHANNEL"))
        self.assertEqual(channels[0], channels[1])

    def test_external_pll_is_validated_without_mutation_or_reinstantiation(self):
        pll = GTYQuadPLL(Signal(), 161.1328125e6, 25.78125e9,
            qpll="qpll0", qpll_params=PMA_USP_GTY_25G_BASER.qpll_params)
        before = dict(pll.gty_params)
        # An externally owned PLL can already be finalized by its owner.
        fragment = pll.get_fragment()
        common_before = primitive_parameters(fragment, "GTYE4_COMMON")
        phy = USP_GTY_25G_BASER(None, data_pads(), 100e6, pll=pll, with_csr=False)
        phy_fragment = phy.get_fragment()
        self.assertEqual(pll.gty_params, before)
        self.assertEqual(primitive_parameters(fragment, "GTYE4_COMMON"), common_before)
        self.assertFalse(any(isinstance(s, Instance) and s.of == "GTYE4_COMMON"
            for s in phy_fragment.specials))

    def test_incompatible_external_plls_are_rejected(self):
        for pll in (
            GTYQuadPLL(Signal(), 156.25e6, 10.3125e9),
            GTYQuadPLL(Signal(), 156.25e6, 25.78125e9, qpll="qpll1"),
            GTYQuadPLL(Signal(), 156.25e6, 25.78125e9, qpll="qpll0"),
            GTH4QuadPLL(Signal(), 156.25e6, 10.3125e9),
        ):
            with self.subTest(config=pll.config), self.assertRaisesRegex(ValueError, "External PLL"):
                USP_GTY_25G_BASER(None, data_pads(), 100e6, pll=pll, with_csr=False)

    def test_existing_10g_external_pll_needs_no_tuning(self):
        pll = GTYQuadPLL(Signal(), 156.25e6, 10.3125e9)
        phy = USP_GTY_10G_BASER(None, data_pads(), 100e6, pll=pll, with_csr=False)
        self.assertIs(phy.pll, pll)
        phy.get_fragment()
