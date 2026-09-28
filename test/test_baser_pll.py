#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import ClockDomainsRenamer, Instance, Module, Record, Signal
from migen.fhdl.visit import NodeVisitor

from liteiclink.serdes.gth4_ultrascale import GTH4QuadPLL
from liteiclink.serdes.gtx_7series import GTXQuadPLL
from liteiclink.serdes.gty_ultrascale import GTYQuadPLL

from liteeth.phy.serial.baser.wrappers.k7_gtx import K7_GTX_10G_BASER
from liteeth.phy.serial.baser.wrappers.usp_gt import USP_GTH_10G_BASER, USP_GTY_10G_BASER


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


class Assignments(NodeVisitor):
    def __init__(self, signal):
        self.signal  = signal
        self.sources = []

    def visit_Assign(self, node):
        if node.l is self.signal:
            self.sources.append(node.r)


class TestBASERSharedPLL(unittest.TestCase):
    def test_two_channels_have_one_pll_reset_owner(self):
        for pll_cls, phy_cls, primitive in (
            (GTXQuadPLL, K7_GTX_10G_BASER, "GTXE2_COMMON"),
            (GTH4QuadPLL, USP_GTH_10G_BASER, "GTHE4_COMMON"),
            (GTYQuadPLL, USP_GTY_10G_BASER, "GTYE4_COMMON"),
        ):
            for master in (0, 1):
                with self.subTest(phy=phy_cls.__name__, master=master):
                    dut = Module()
                    dut.submodules.pll = pll = pll_cls(Signal(), 156.25e6, 10.3125e9)
                    phys = []
                    for channel in range(2):
                        kwargs = dict(with_csr=False, pll_master=(channel == master))
                        if phy_cls is K7_GTX_10G_BASER:
                            phy = phy_cls(pll, data_pads(), 100e6, **kwargs)
                        else:
                            phy = phy_cls(Signal(), data_pads(), 100e6, pll=pll, **kwargs)
                        phys.append(phy)
                        setattr(dut.submodules, f"phy{channel}", ClockDomainsRenamer({
                            name: f"{name}{channel}" for name in
                            ("eth_tx", "eth_rx", "eth_tx_raw", "eth_rx_raw")
                        })(phy))
                    fragment = dut.get_fragment()
                    assignments = Assignments(pll.reset)
                    assignments.visit(fragment.comb)
                    self.assertEqual(len(assignments.sources), 1)
                    self.assertIs(assignments.sources[0], phys[master].pma.tx_init.pllreset)
                    instances = [s.of for s in fragment.specials if isinstance(s, Instance)]
                    self.assertEqual(instances.count(primitive), 1)
                    self.assertEqual(instances.count(primitive.replace("COMMON", "CHANNEL")), 2)

    def test_single_channel_keeps_reset_ownership_by_default(self):
        phy = USP_GTY_10G_BASER(Signal(), data_pads(), 100e6, with_csr=False)
        assignments = Assignments(phy.pll.reset)
        assignments.visit(phy.get_fragment().comb)
        self.assertEqual(len(assignments.sources), 1)
        self.assertIs(assignments.sources[0], phy.pma.tx_init.pllreset)

    def test_internal_pll_requires_reset_owner(self):
        with self.assertRaisesRegex(ValueError, "external PLL"):
            USP_GTY_10G_BASER(Signal(), data_pads(), 100e6, pll_master=False)
