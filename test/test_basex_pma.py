#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal

from liteeth.phy.serial.basex.pcs import PCSGearbox as LegacyPCSGearbox
from liteeth.phy.serial.basex.pma.gearbox import PCSGearbox
from liteeth.phy.serial.basex.wrappers.k7_gtx import K7_1000BASEX, K7_2500BASEX


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


class TestBASEXPMA(unittest.TestCase):
    def test_gearbox_import_identity(self):
        self.assertIs(LegacyPCSGearbox, PCSGearbox)

    def test_k7_public_handles_and_csr_layout(self):
        for cls in (K7_1000BASEX, K7_2500BASEX):
            for with_csr in (False, True):
                with self.subTest(phy=cls.__name__, with_csr=with_csr):
                    phy = cls(Signal(), data_pads(), 100e6, with_csr=with_csr)
                    for name in ("cd_eth_tx", "cd_eth_rx", "cd_eth_tx_half", "cd_eth_rx_half",
                        "reset", "txoutclk", "rxoutclk", "gearbox", "pll", "tx_mmcm", "rx_mmcm",
                        "tx_init", "rx_init"):
                        self.assertIs(getattr(phy, name), getattr(phy.pma, name))
                    self.assertEqual([(csr.name, csr.size, csr.description) for csr in phy.get_csrs()],
                        [("reset", 1, "PHY reset.")] if with_csr else [])
                    fragment = phy.get_fragment()
                    # Aliasing public handles must not instantiate a second clock/init/gearbox.
                    instances = [special.of for special in fragment.specials if isinstance(special, Instance)]
                    self.assertEqual(instances.count("GTXE2_CHANNEL"), 1)
                    self.assertEqual(instances.count("MMCME2_ADV"), 2)


if __name__ == "__main__":
    unittest.main()
