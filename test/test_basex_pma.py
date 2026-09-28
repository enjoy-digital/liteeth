#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace
from unittest import mock

from migen import Instance, Record, Signal

from litex.gen import LiteXContext

from liteeth.phy.serial.basex.pcs import PCSGearbox as LegacyPCSGearbox
from liteeth.phy.serial.basex.pma.gearbox import PCSGearbox
from liteeth.phy.serial.basex.wrappers.k7_gtx import K7_1000BASEX, K7_2500BASEX
from liteeth.phy.serial.basex.wrappers.v7_gth import V7_1000BASEX, V7_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gty import USP_GTY_1000BASEX, USP_GTY_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gth import USP_GTH_1000BASEX, USP_GTH_2500BASEX
from liteeth.phy.serial.basex.wrappers.ku_gth import KU_1000BASEX, KU_2500BASEX
from liteeth.phy.serial.basex.wrappers.a7_gtp import A7_1000BASEX, A7_2500BASEX
from liteeth.phy.serial.gtp_7series import QPLLChannel
from liteeth.phy.serial.basex.wrappers.us_lvds import US_LVDS_1000BASEX


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


class TestBASEXPMA(unittest.TestCase):
    def test_gearbox_import_identity(self):
        self.assertIs(LegacyPCSGearbox, PCSGearbox)

    def test_public_handles_and_csr_layout(self):
        for cls, primitive, kwargs in (
            (K7_1000BASEX, "GTXE2_CHANNEL", {}),
            (K7_2500BASEX, "GTXE2_CHANNEL", {}),
            (V7_1000BASEX, "GTHE2_CHANNEL", {}),
            (V7_2500BASEX, "GTHE2_CHANNEL", {}),
            (USP_GTY_1000BASEX, "GTYE4_CHANNEL", {}),
            (USP_GTY_2500BASEX, "GTYE4_CHANNEL", {"refclk_freq": 156.25e6}),
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
                    if primitive in ("GTXE2_CHANNEL", "GTHE2_CHANNEL"):
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

    def test_us_and_usp_lvds_wrappers_keep_constraints_and_csrs(self):
        for usp in (False, True):
            for with_csr in (False, True):
                with self.subTest(usp=usp, with_csr=with_csr):
                    constraints = []
                    commands = []
                    platform = SimpleNamespace(
                        add_false_path_constraints=lambda *clocks: constraints.append(clocks),
                        add_platform_command=lambda command, **signals: commands.append((command, signals)),
                    )
                    pads = Record([("tx_p", 1), ("tx_n", 1), ("rx_p", 1), ("rx_n", 1), ("rst_n", 1)])
                    with mock.patch.object(LiteXContext, "platform", platform), \
                         mock.patch.object(LiteXContext, "top", SimpleNamespace(sys_clk_freq=100e6)):
                        phy = US_LVDS_1000BASEX(pads, Signal(), 100e6, usp=usp,
                            iodelay_clk_freq=300e6 if usp else 200e6, with_csr=with_csr)
                    self.assertEqual(constraints, [
                        (phy.crg.cd_eth_rx.clk, phy.crg.cd_eth_rx_div.clk),
                        (phy.crg.cd_eth_tx.clk, phy.crg.cd_eth_tx_div.clk),
                    ])
                    self.assertEqual(commands, [
                        ("set_property CLOCK_DELAY_GROUP eth_rx_ser_clks [get_nets {{{ser} {div}}}]",
                            {"ser" : phy.crg.cd_eth_rx_ser.clk, "div" : phy.crg.cd_eth_rx_div.clk}),
                        ("set_property CLOCK_DELAY_GROUP eth_tx_ser_clks [get_nets {{{ser} {div}}}]",
                            {"ser" : phy.crg.cd_eth_tx_ser.clk, "div" : phy.crg.cd_eth_tx_div.clk}),
                    ])
                    if with_csr:
                        self.assertEqual(phy.cdr_control.size, 4)
                        self.assertEqual(phy.cdr_status.size, 40)
                    fragment = phy.get_fragment()
                    instances = [s.of for s in fragment.specials if isinstance(s, Instance)]
                    self.assertEqual(instances.count("MMCME4_ADV" if usp else "MMCME3_ADV"), 1)
                    self.assertEqual(instances.count("ISERDESE3"), 2)
                    self.assertEqual(instances.count("OSERDESE3"), 1)


if __name__ == "__main__":
    unittest.main()
