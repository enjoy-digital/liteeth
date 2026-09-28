#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace

from migen import ClockDomain, Instance, Record, Signal

from litex.gen.sim import run_simulation

from liteiclink.serdes.gtx_7series import GTXQuadPLL

from liteeth.phy.serial.gtp_7series import QPLLChannel
from liteeth.phy.a7_1000basex import A7_1000BASEX, A7_2500BASEX
from liteeth.phy.serial.baser.wrappers.a7_gtp import A7_GTP_5G_BASER
from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY
from liteeth.phy.k7_1000basex import K7_1000BASEX, K7_2500BASEX
from liteeth.phy.serial.baser.wrappers.k7_gtx import K7_GTX_10G_BASER, K7_GTX_5G_BASER
from liteeth.phy.serial.baser.wrappers.usp_gt import (
    USP_GTH_10G_BASER, USP_GTH_5G_BASER,
    USP_GTY_10G_BASER, USP_GTY_5G_BASER, USP_GTY_25G_BASER,
)


def data_pads():
    return Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])


# PHY Portability ----------------------------------------------------------------------------------

class TestPHYPortability(unittest.TestCase):
    def test_baser_wrappers_elaborate(self):
        variants = [
            ("a7_5g",     lambda: A7_GTP_5G_BASER(QPLLChannel(0), data_pads(), 100e6, 125e6), "GTPE2_CHANNEL"),
            ("k7_5g",     lambda: K7_GTX_5G_BASER(GTXQuadPLL(Signal(), 156.25e6, 5.15625e9), data_pads(), 100e6), "GTXE2_CHANNEL"),
            ("k7_10g",    lambda: K7_GTX_10G_BASER(GTXQuadPLL(Signal(), 156.25e6, 10.3125e9), data_pads(), 100e6), "GTXE2_CHANNEL"),
            ("usp_gth_5g", lambda: USP_GTH_5G_BASER(Signal(), data_pads(), 100e6), "GTHE4_CHANNEL"),
            ("usp_gth_10g", lambda: USP_GTH_10G_BASER(Signal(), data_pads(), 100e6), "GTHE4_CHANNEL"),
            ("usp_gty_5g", lambda: USP_GTY_5G_BASER(Signal(), data_pads(), 100e6), "GTYE4_CHANNEL"),
            ("usp_gty_10g", lambda: USP_GTY_10G_BASER(Signal(), data_pads(), 100e6), "GTYE4_CHANNEL"),
            ("usp_gty_25g", lambda: USP_GTY_25G_BASER(Signal(), data_pads(), 100e6), "GTYE4_CHANNEL"),
        ]
        for name, make_phy, primitive in variants:
            with self.subTest(phy=name):
                phy = make_phy()
                fragment = phy.get_fragment()
                primitives = {special.of for special in fragment.specials
                    if isinstance(special, Instance)}
                self.assertIn(primitive, primitives)
                self.assertEqual(len(phy.sink.data), 64)
                self.assertEqual(len(phy.source.data), 64)
                self.assertEqual(len(phy.pma.tx_header), 2)
                self.assertEqual(len(phy.pma.rx_header), 2)
                self.assertTrue(hasattr(phy, "cd_eth_tx"))
                self.assertTrue(hasattr(phy, "cd_eth_rx"))
                self.assertEqual(phy._control.size, 6)
                self.assertEqual(phy._status.size, 13)
                self.assertTrue(hasattr(phy, "_rx_prbs_errors"))

    def test_a7_without_prbs_keeps_core_status(self):
        phy = A7_GTP_5G_BASER(QPLLChannel(0), data_pads(), 100e6, 125e6,
            with_prbs=False)
        phy.get_fragment()
        self.assertEqual(phy._control.size, 6)
        self.assertEqual(phy._status.size, 13)
        self.assertFalse(hasattr(phy, "_rx_prbs_errors"))

    def test_1000_and_2500_basex_wrappers_elaborate(self):
        variants = [
            ("a7_1000", A7_1000BASEX, "GTPE2_CHANNEL"),
            ("a7_2500", A7_2500BASEX, "GTPE2_CHANNEL"),
            ("k7_1000", K7_1000BASEX, "GTXE2_CHANNEL"),
            ("k7_2500", K7_2500BASEX, "GTXE2_CHANNEL"),
        ]
        for name, phy_cls, primitive in variants:
            with self.subTest(phy=name):
                if name.startswith("a7"):
                    phy = phy_cls(QPLLChannel(0), data_pads(), 100e6, with_csr=False)
                else:
                    phy = phy_cls(Signal(), data_pads(), 100e6, with_csr=False)
                fragment = phy.get_fragment()
                primitives = {special.of for special in fragment.specials
                    if isinstance(special, Instance)}
                self.assertIn(primitive, primitives)
                self.assertTrue(hasattr(phy, "pcs"))
                if name.startswith("k7"):
                    gtx = next(special for special in fragment.specials
                        if isinstance(special, Instance) and special.of == primitive)
                    gtx_params = {item.name: item.value for item in gtx.items
                        if isinstance(item, Instance.Parameter)}
                    clk25_div = {"k7_1000": 8, "k7_2500": 5}[name]
                    self.assertEqual(gtx_params["RX_CLK25_DIV"].value, clk25_div)
                    self.assertEqual(gtx_params["TX_CLK25_DIV"].value, clk25_div)
                    rxcdr_cfg = {
                        "k7_1000": 0x03000023ff10100020,
                        "k7_2500": 0x03000023ff10200020,
                    }[name]
                    self.assertEqual(gtx_params["RXCDR_CFG"].value, rxcdr_cfg)
                if name == "k7_2500":
                    self.assertEqual(phy.pll.config["clkin"], 125e6)

    def test_gapped_prbs_counter_saturates_and_resets(self):
        class CounterDUT(LiteEthBASERPHY):
            def __init__(self):
                self.cd_eth_rx = ClockDomain()
                self.pcs = SimpleNamespace(rx_error_count=Signal(7))
                self.rx_prbs31_enable = Signal()
                self.rx_ce = Signal()
                self.add_prbs_counter(4, rx_ce=self.rx_ce)

        dut = CounterDUT()
        samples = []

        def stimulus():
            yield dut.rx_prbs31_enable.eq(1)
            yield dut.pcs.rx_error_count.eq(7)
            for _ in range(64):
                yield
            samples.append((yield dut.rx_prbs_errors))

            yield dut.rx_ce.eq(1)
            for _ in range(64):
                yield
            samples.append((yield dut.rx_prbs_errors))

            yield dut.rx_prbs31_enable.eq(0)
            for _ in range(64):
                yield
            samples.append((yield dut.rx_prbs_errors))

        run_simulation(dut, stimulus(), clocks={"sys": 10, "eth_rx": 10})
        self.assertEqual(samples, [0, 15, 0])
