#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os

from migen import *

from litex.gen import *


# LiteEth PHY BASE-R PCS ---------------------------------------------------------------------------

class LiteEthPHYBaseRPCS(LiteXModule):
    """64b/66b BASE-R PCS.

    The PCS presents an XGMII interface and a 64-bit data/2-bit header interface
    to the transceiver gearbox.  Clause 49 and Clause 129 use the same block
    encoding; the operating speed is selected by the PCS clock frequency.
    """
    def __init__(self, platform, tx_clk, tx_rst, rx_clk, rx_rst, clk_freq=78.125e6):
        # XGMII.
        self.xgmii_txd = Signal(64)
        self.xgmii_txc = Signal(8)
        self.xgmii_rxd = Signal(64)
        self.xgmii_rxc = Signal(8)

        # Transceiver gearbox.
        self.serdes_tx_data = Signal(64)
        self.serdes_tx_hdr  = Signal(2)
        self.serdes_rx_data = Signal(64)
        self.serdes_rx_hdr  = Signal(2)
        self.serdes_rx_slip = Signal()

        # Status.
        self.tx_bad_block      = Signal()
        self.rx_error_count    = Signal(7)
        self.rx_bad_block      = Signal()
        self.rx_sequence_error = Signal()
        self.rx_block_lock     = Signal()
        self.rx_high_ber       = Signal()
        self.rx_status         = Signal()
        self.rx_reset_req      = Signal()

        # PRBS31 test mode.
        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()

        # # #

        self.specials += Instance("eth_phy_10g_tx",
            p_DATA_WIDTH       = 64,
            p_CTRL_WIDTH       = 8,
            p_HDR_WIDTH        = 2,
            p_BIT_REVERSE      = 1,
            p_PRBS31_ENABLE    = 1,
            i_clk              = tx_clk,
            i_rst              = tx_rst,
            i_xgmii_txd        = self.xgmii_txd,
            i_xgmii_txc        = self.xgmii_txc,
            o_serdes_tx_data   = self.serdes_tx_data,
            o_serdes_tx_hdr    = self.serdes_tx_hdr,
            o_tx_bad_block     = self.tx_bad_block,
            i_cfg_tx_prbs31_enable = self.tx_prbs31_enable,
        )
        self.specials += Instance("eth_phy_10g_rx",
            p_DATA_WIDTH         = 64,
            p_CTRL_WIDTH         = 8,
            p_HDR_WIDTH          = 2,
            p_BIT_REVERSE        = 1,
            p_PRBS31_ENABLE      = 1,
            p_COUNT_125US        = int(round(clk_freq*125e-6)),
            i_clk                = rx_clk,
            i_rst                = rx_rst,
            o_xgmii_rxd          = self.xgmii_rxd,
            o_xgmii_rxc          = self.xgmii_rxc,
            i_serdes_rx_data     = self.serdes_rx_data,
            i_serdes_rx_hdr      = self.serdes_rx_hdr,
            o_serdes_rx_bitslip  = self.serdes_rx_slip,
            o_serdes_rx_reset_req = self.rx_reset_req,
            o_rx_error_count     = self.rx_error_count,
            o_rx_bad_block       = self.rx_bad_block,
            o_rx_sequence_error  = self.rx_sequence_error,
            o_rx_block_lock      = self.rx_block_lock,
            o_rx_high_ber        = self.rx_high_ber,
            o_rx_status          = self.rx_status,
            i_cfg_rx_prbs31_enable = self.rx_prbs31_enable,
        )

        rtl_dir = os.path.join(os.path.dirname(__file__), "rtl")
        for filename in [
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
        ]:
            platform.add_source(os.path.join(rtl_dir, filename))
