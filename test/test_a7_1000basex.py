#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace
from unittest import mock

from migen import Signal

import liteeth.phy.a7_1000basex as a7_1000basex
from liteeth.phy.a7_gtp import QPLLChannel
from liteeth.phy.pcs_1000basex import PCS


class TestA72500BASEX(unittest.TestCase):
    @staticmethod
    def make_pads():
        return SimpleNamespace(
            rxn = Signal(),
            rxp = Signal(),
            txn = Signal(),
            txp = Signal(),
        )

    def test_pcs_kwargs_override_default_timer_clock(self):
        pcs_config = {}

        def make_pcs(**kwargs):
            pcs_config.update(kwargs)
            return PCS(**kwargs)

        with mock.patch.object(a7_1000basex, "PCS", side_effect=make_pcs):
            a7_1000basex.A7_2500BASEX(
                qpll_channel = QPLLChannel(0),
                data_pads    = self.make_pads(),
                sys_clk_freq = 100e6,
                with_csr     = False,
                pcs_kwargs   = {"eth_tx_clk_freq": 125e6},
            )

        self.assertEqual(pcs_config["eth_tx_clk_freq"], 125e6)
        self.assertTrue(pcs_config["lsb_first"])

    def test_optional_pcs_buffers_are_exposed_at_phy_boundary(self):
        dut = a7_1000basex.A7_2500BASEX(
            qpll_channel     = QPLLChannel(0),
            data_pads        = self.make_pads(),
            sys_clk_freq     = 100e6,
            with_csr         = False,
            with_pcs_buffers = True,
        )

        self.assertIs(dut.sink, dut.tx_pcs_buffer.sink)
        self.assertIs(dut.source, dut.rx_pcs_buffer.source)


if __name__ == "__main__":
    unittest.main()
