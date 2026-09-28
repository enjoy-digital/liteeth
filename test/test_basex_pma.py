#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Instance, Record, Signal

from liteeth.phy.serial.basex.pcs import PCSGearbox as LegacyPCSGearbox
from liteeth.phy.serial.basex.pma.gearbox import PCSGearbox
from liteeth.phy.serial.basex.wrappers.k7_gtx import K7_1000BASEX, K7_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gth import USP_GTH_1000BASEX, USP_GTH_2500BASEX
from liteeth.phy.serial.basex.wrappers.ku_gth import KU_1000BASEX, KU_2500BASEX
from liteeth.phy.serial.basex.wrappers.a7_gtp import A7_1000BASEX, A7_2500BASEX
from liteeth.phy.serial.gtp_7series import QPLLChannel


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


class TestBASEXPMA(unittest.TestCase):
    def test_gearbox_import_identity(self):
        self.assertIs(LegacyPCSGearbox, PCSGearbox)

    def test_public_handles_and_csr_layout(self):
        for cls, primitive, kwargs in (
            (K7_1000BASEX, "GTXE2_CHANNEL", {}),
            (K7_2500BASEX, "GTXE2_CHANNEL", {}),
            (USP_GTH_1000BASEX, "GTHE4_CHANNEL", {}),
            (USP_GTH_2500BASEX, "GTHE4_CHANNEL", {"refclk_freq": 156.25e6}),
            (KU_1000BASEX, "GTHE3_CHANNEL", {}),
            (KU_2500BASEX, "GTHE3_CHANNEL", {"refclk_freq": 156.25e6}),
        ):
            for with_csr in (False, True):
                with self.subTest(phy=cls.__name__, with_csr=with_csr):
                    phy = cls(Signal(), data_pads(), 100e6, with_csr=with_csr, **kwargs)
                    for name in ("cd_eth_tx", "cd_eth_rx", "cd_eth_tx_half", "cd_eth_rx_half",
                        "reset", "txoutclk", "rxoutclk", "gearbox", "pll"):
                        self.assertIs(getattr(phy, name), getattr(phy.pma, name))
                    self.assertEqual([(csr.name, csr.size, csr.description) for csr in phy.get_csrs()],
                        [("reset", 1, "PHY reset.")] if with_csr else [])
                    fragment = phy.get_fragment()
                    # Aliasing public handles must not instantiate a second clock/init/gearbox.
                    instances = [special.of for special in fragment.specials if isinstance(special, Instance)]
                    self.assertEqual(instances.count(primitive), 1)
                    if primitive == "GTXE2_CHANNEL":
                        for name in ("tx_mmcm", "rx_mmcm", "tx_init", "rx_init"):
                            self.assertIs(getattr(phy, name), getattr(phy.pma, name))
                        self.assertEqual(instances.count("MMCME2_ADV"), 2)

    def test_a7_mutable_parameters_and_deferred_instantiation(self):
        for cls in (A7_1000BASEX, A7_2500BASEX):
            for channel in (0, 1):
                for cm in ("PLL", "MMCM"):
                    with self.subTest(phy=cls.__name__, channel=channel, cm=cm):
                        phy = cls(QPLLChannel(channel), data_pads(), 100e6,
                            tx_cm_type=cm, rx_cm_type=cm, with_pcs_buffers=True)
                        self.assertIs(phy.gtp_params, phy.pma.gtp_params)
                        for name in ("tx_cm", "rx_cm", "tx_init", "rx_init", "gearbox"):
                            self.assertIs(getattr(phy, name), getattr(phy.pma, name))
                        self.assertFalse(any(isinstance(s, Instance) and s.of == "GTPE2_CHANNEL"
                            for s in phy.pma._fragment.specials))
                        phy.gtp_params["i_TXDIFFCTRL"] = 0b1010
                        fragment = phy.get_fragment()
                        channels = [s for s in fragment.specials
                            if isinstance(s, Instance) and s.of == "GTPE2_CHANNEL"]
                        self.assertEqual(len(channels), 1)
                        self.assertEqual(channels[0].get_io("TXDIFFCTRL").value, 0b1010)
                        self.assertEqual(channels[0].get_io("TXSYSCLKSEL").value, 0b11 if channel else 0)

    def test_a7_finalization_hook_can_override_parameters(self):
        class CustomPHY(A7_1000BASEX):
            def do_finalize(self):
                self.gtp_params = dict(self.gtp_params, i_TXDIFFCTRL=0b1001)
                super().do_finalize()

        phy = CustomPHY(QPLLChannel(0), data_pads(), 100e6, with_csr=False)
        fragment = phy.get_fragment()
        channels = [s for s in fragment.specials if isinstance(s, Instance) and s.of == "GTPE2_CHANNEL"]
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].get_io("TXDIFFCTRL").value, 0b1001)


if __name__ == "__main__":
    unittest.main()
