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


class TestA75000BASEX(TestA72500BASEX):
    def test_40bit_gtp_and_parallel_pcs_configuration(self):
        dut = a7_1000basex.A7_5000BASEX(
            qpll_channel = QPLLChannel(0),
            data_pads    = self.make_pads(),
            sys_clk_freq = 125e6,
            with_csr     = False,
            tx_cm_type   = "MMCM",
            rx_cm_type   = "MMCM",
            rx_polarity  = 1,
        )

        self.assertEqual(dut.dw, 32)
        self.assertEqual(dut.gtp_dw, 40)
        self.assertEqual(dut.gtp_clk_freq, 312.5e6)
        self.assertEqual(dut.gtp_tx_usrclk_domain, "eth_tx_half")
        self.assertEqual(dut.gtp_rx_usrclk_domain, "eth_rx_half")
        self.assertEqual(dut.gtp_tx_clock_domain, "eth_tx")
        self.assertEqual(dut.gtp_rx_clock_domain, "eth_rx")
        self.assertEqual(dut.gtp_params["i_TXUSRCLK"].cd, "eth_tx_half")
        self.assertEqual(dut.gtp_params["i_TXUSRCLK2"].cd, "eth_tx")
        self.assertEqual(dut.gtp_params["i_RXUSRCLK"].cd, "eth_rx_half")
        self.assertEqual(dut.gtp_params["i_RXUSRCLK2"].cd, "eth_rx")
        self.assertEqual(len(dut.pcs.tbi_tx), 40)
        self.assertEqual(len(dut.pcs.tbi_rx), 40)
        self.assertFalse(hasattr(dut, "gearbox"))
        self.assertEqual(dut.gtp_params["p_RX_DATA_WIDTH"], 40)
        self.assertEqual(dut.gtp_params["p_TX_DATA_WIDTH"], 40)
        self.assertEqual(dut.gtp_params["p_ALIGN_COMMA_WORD"], 2)
        self.assertEqual(dut.gtp_params["p_RXOUT_DIV"], 1)
        self.assertEqual(dut.gtp_params["p_TXOUT_DIV"], 1)
        self.assertEqual(
            dut.gtp_params["p_RXCDR_CFG"],
            0x0000107FE406001041010,
        )
        self.assertEqual(len(dut.gtp_params["i_TXDATA"]), 32)
        self.assertEqual(len(dut.gtp_params["i_TXCHARDISPMODE"]), 4)
        self.assertEqual(len(dut.gtp_params["i_TXCHARDISPVAL"]), 4)
        self.assertEqual(len(dut.gtp_params["o_RXDATA"]), 32)
        self.assertEqual(len(dut.gtp_params["o_RXCHARISK"]), 4)
        self.assertEqual(len(dut.gtp_params["o_RXDISPERR"]), 4)
        self.assertIs(dut.gtp_params["i_LOOPBACK"], dut.loopback)
        self.assertIs(dut.gtp_params["i_TXPRBSSEL"], dut.tx_prbs_config)
        self.assertIs(dut.gtp_params["i_RXPRBSSEL"], dut.rx_prbs_config)
        self.assertIs(dut.gtp_params["o_RXPRBSERR"], dut.rx_prbs_error)
        self.assertIs(dut.gtp_params["o_RXCDRLOCK"], dut.rx_cdr_lock)
        self.assertIs(dut.gtp_params["i_RXPOLARITY"], dut.rx_polarity_effective)
        self.assertIsNot(dut.gtp_params["i_RXMCOMMAALIGNEN"], dut.pcs.align)
        self.assertIsNot(dut.gtp_params["i_RXPCOMMAALIGNEN"], dut.pcs.align)

    def test_20bit_modes_keep_half_rate_gtp_clock_domains(self):
        dut = a7_1000basex.A7_2500BASEX(
            qpll_channel = QPLLChannel(0),
            data_pads    = self.make_pads(),
            sys_clk_freq = 125e6,
            with_csr     = False,
            tx_cm_type   = "MMCM",
            rx_cm_type   = "MMCM",
        )

        self.assertEqual(dut.gtp_tx_clock_domain, "eth_tx_half")
        self.assertEqual(dut.gtp_rx_clock_domain, "eth_rx_half")


if __name__ == "__main__":
    unittest.main()
