#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal
from migen.fhdl.structure import Constant

from liteiclink.serdes.gth4_ultrascale import GTH4QuadPLL
from liteiclink.serdes.gty_ultrascale import GTYQuadPLL

from liteeth.phy.serial.baser.wrappers.usp_gt import (
    USP_GTH_5G_BASER, USP_GTH_10G_BASER,
    USP_GTY_5G_BASER, USP_GTY_10G_BASER, USP_GTY_25G_BASER,
)


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


class TestBASERReferenceClock(unittest.TestCase):
    def test_owned_pll_reference_routing(self):
        for cls in (USP_GTH_5G_BASER, USP_GTH_10G_BASER,
            USP_GTY_5G_BASER, USP_GTY_10G_BASER, USP_GTY_25G_BASER):
            for source in ("dedicated", "differential", "fabric"):
                with self.subTest(phy=cls.__name__, source=source):
                    refclk = Record([("p", 1), ("n", 1)]) if source == "differential" else Signal()
                    phy = cls(refclk, data_pads(), 100e6, with_csr=False,
                        refclk_from_fabric=(source == "fabric"))
                    fragment = phy.get_fragment()
                    commons = [s for s in fragment.specials if isinstance(s, Instance)
                        and s.of in ("GTYE4_COMMON", "GTHE4_COMMON")]
                    self.assertEqual(len(commons), 1)
                    common = commons[0]
                    index = int(phy.pll.config["qpll"][-1])
                    selected = f"GTGREFCLK{index}" if source == "fabric" else f"GTREFCLK0{index}"
                    unused = f"GTREFCLK0{index}" if source == "fabric" else f"GTGREFCLK{index}"
                    self.assertNotIsInstance(common.get_io(selected), Constant)
                    self.assertEqual(common.get_io(unused).value, 0)
                    self.assertEqual(common.get_io(f"QPLL{index}REFCLKSEL").value,
                        0b111 if source == "fabric" else 0b001)
                    buffers = [s for s in fragment.specials if isinstance(s, Instance)
                        and s.of == "IBUFDS_GTE4"]
                    self.assertEqual(len(buffers), int(source == "differential"))

    def test_external_pll_owns_its_reference_routing(self):
        for phy_cls, pll_cls in ((USP_GTH_10G_BASER, GTH4QuadPLL), (USP_GTY_10G_BASER, GTYQuadPLL)):
            with self.subTest(phy=phy_cls.__name__):
                pll = pll_cls(Signal(), 156.25e6, 10.3125e9, refclk_from_fabric=True)
                phy = phy_cls(None, data_pads(), 100e6, pll=pll, with_csr=False)
                primitives = [s.of for s in phy.get_fragment().specials if isinstance(s, Instance)]
                self.assertNotIn("IBUFDS_GTE4", primitives)
                self.assertNotIn("GTYE4_COMMON", primitives)
                self.assertNotIn("GTHE4_COMMON", primitives)
                self.assertIs(phy.pll, pll)
