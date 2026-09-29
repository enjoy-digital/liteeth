#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from liteeth.mac.axis import LiteEthMACAXIStream

# UltraScale+ CMAC ---------------------------------------------------------------------------------

class USP_CMAC_100G(LiteEthMACAXIStream):
    """100G frame interface to a generated AMD CMAC (CAUI-4, AXI, no RS-FEC).

    The target owns IP generation, GT/CMAC placement and pin constraints. Use the configuration
    in bench/cmac_100g.tcl. The IP includes GTs and shared logic; its TX user clock drives both
    MAC client domains as required by PG203. init_clk must be free-running at 100 MHz.
    """
    def __init__(self, refclk_pads, data_pads, init_clk, reset, ip_name="liteeth_cmac", **kwargs):
        if any(len(getattr(data_pads, name)) != 4 for name in ["rxp", "rxn", "txp", "txn"]):
            raise ValueError("CAUI-4 requires four differential transceiver lanes.")
        LiteEthMACAXIStream.__init__(self, dw=512, **kwargs)
        self.cd_eth_tx      = ClockDomain("eth_tx")
        self.cd_eth_rx      = ClockDomain("eth_rx")
        self.tx_reset       = Signal()
        self.rx_reset       = Signal()
        self.rx_aligned     = Signal()
        self.rx_local_fault = Signal()
        self.tx_underflow   = Signal()
        self.tx_overflow    = Signal()
        self.link_up        = Signal()

        # # #

        tx_clk    = Signal()
        tx_ready  = Signal()
        rx_status = Signal()
        self.comb += [
            self.cd_eth_tx.clk.eq(tx_clk),
            self.cd_eth_rx.clk.eq(tx_clk),
            # Do not drain committed frames while the transmitter sends remote fault.
            self.tx.ready.eq(tx_ready & self.rx_aligned),
        ]
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset | self.tx_reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset | self.rx_reset),
            MultiReg(rx_status, self.link_up),
        ]

        # CMAC ------------------------------------------------------------------------------------
        self.cmac_params = dict(
            # Transceiver.
            i_gt_rxp_in                = data_pads.rxp,
            i_gt_rxn_in                = data_pads.rxn,
            o_gt_txp_out               = data_pads.txp,
            o_gt_txn_out               = data_pads.txn,
            i_gt_ref_clk_p             = refclk_pads.p,
            i_gt_ref_clk_n             = refclk_pads.n,
            # Clocks and resets.
            i_init_clk                 = init_clk,
            i_sys_reset                = reset,
            i_gt_loopback_in           = 0,
            i_gtwiz_reset_tx_datapath   = 0,
            i_gtwiz_reset_rx_datapath   = 0,
            o_gt_txusrclk2              = tx_clk,
            i_rx_clk                   = tx_clk,
            i_core_tx_reset            = 0,
            i_core_rx_reset            = 0,
            o_usr_tx_reset             = self.tx_reset,
            o_usr_rx_reset             = self.rx_reset,
            # Control and status.
            i_ctl_rx_enable            = 1,
            i_ctl_rx_force_resync      = 0,
            i_ctl_rx_test_pattern      = 0,
            i_ctl_tx_enable            = self.rx_aligned,
            i_ctl_tx_send_rfi          = ~self.rx_aligned,
            i_ctl_tx_send_lfi          = 0,
            i_ctl_tx_send_idle         = 0,
            i_ctl_tx_test_pattern      = 0,
            i_tx_preamblein            = 0,
            o_stat_rx_aligned          = self.rx_aligned,
            o_stat_rx_status           = rx_status,
            o_stat_rx_local_fault      = self.rx_local_fault,
            o_tx_unfout                = self.tx_underflow,
            o_tx_ovfout                = self.tx_overflow,
            # Frame streams.
            o_tx_axis_tready           = tx_ready,
            i_tx_axis_tvalid           = self.tx.valid & self.rx_aligned,
            i_tx_axis_tdata            = self.tx.data,
            i_tx_axis_tkeep            = self.tx.keep,
            i_tx_axis_tlast            = self.tx.last,
            i_tx_axis_tuser            = self.tx.user,
            o_rx_axis_tvalid           = self.rx.valid,
            o_rx_axis_tdata            = self.rx.data,
            o_rx_axis_tkeep            = self.rx.keep,
            o_rx_axis_tlast            = self.rx.last,
            o_rx_axis_tuser            = self.rx.user,
            # Unused DRP interface.
            i_core_drp_reset           = reset,
            i_drp_clk                  = init_clk,
            i_drp_addr                 = 0,
            i_drp_di                   = 0,
            i_drp_en                   = 0,
            i_drp_we                   = 0,
        )
        self.specials += Instance(ip_name, **self.cmac_params)
