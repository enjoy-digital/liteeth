#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from pathlib import Path

import pytest

from migen import Signal

from liteeth.phy.a7_1000basex import A7_5000BASER


class _QPLLChannel:
    def __init__(self):
        self.index  = 0
        self.reset  = Signal()
        self.lock   = Signal()
        self.clk    = Signal()
        self.refclk = Signal()


class _DataPads:
    def __init__(self):
        self.rxn = Signal()
        self.rxp = Signal()
        self.txn = Signal()
        self.txp = Signal()


class _Platform:
    def __init__(self):
        self.sources  = []
        self.commands = []

    def add_source(self, filename):
        self.sources.append(Path(filename))

    def add_platform_command(self, command, **signals):
        self.commands.append((command, signals))


def test_a7_5000baser_native_gearbox_configuration():
    platform = _Platform()
    dut = A7_5000BASER(
        qpll_channel = _QPLLChannel(),
        data_pads    = _DataPads(),
        sys_clk_freq = 100e6,
        platform     = platform,
    )

    assert dut.dw == 64
    assert dut.linerate == pytest.approx(5.15625e9)
    assert dut.tx_clk_freq == pytest.approx(5.15625e9/32)
    assert dut.rx_clk_freq == pytest.approx(5.15625e9/32)
    assert dut.gtp_params["p_TX_DATA_WIDTH"] == 32
    assert dut.gtp_params["p_RX_DATA_WIDTH"] == 32
    assert dut.gtp_params["p_TXGEARBOX_EN"] == "TRUE"
    assert dut.gtp_params["p_RXGEARBOX_EN"] == "TRUE"
    assert dut.gtp_params["p_GEARBOX_MODE"] == 0b001
    assert dut.gtp_params["p_TXOUT_DIV"] == 1
    assert dut.gtp_params["p_RXOUT_DIV"] == 1
    assert dut.gtp_params["p_RXCDR_CFG"] == 0x0001107FE206021041010
    assert dut.gtp_params["i_LOOPBACK"] is dut.loopback
    assert len(platform.commands) == 7
    assert sum("create_generated_clock" in command for command, _ in platform.commands) == 2
    assert sum("set_multicycle_path" in command for command, _ in platform.commands) == 1
    assert sum("set_false_path -hold" in command for command, _ in platform.commands) == 2

    assert {source.name for source in platform.sources} == {
        "eth_phy_10g_tx.v",
        "eth_phy_10g_rx.v",
        "eth_phy_10g_tx_if.v",
        "eth_phy_10g_rx_if.v",
        "eth_phy_10g_rx_ber_mon.v",
        "eth_phy_10g_rx_frame_sync.v",
        "eth_phy_10g_rx_watchdog.v",
        "xgmii_baser_enc_64.v",
        "xgmii_baser_dec_64.v",
        "lfsr.v",
    }
