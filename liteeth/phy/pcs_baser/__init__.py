#
# This file is part of LiteEth.
#
# This subpackage, containing 802.3ae 10GbE PCS components, has been heavily
# inspired by Alex Forencich's excellent verilog-ethernet project, here:
# https://github.com/alexforencich/verilog-ethernet
#
# While not a straight port, it was initially developed by following the
# verilog-ethernet module architecture and interfaces, and running equivalence
# tests between those modules and the new LiteX modules. The functionality of
# these modules has diverged slightly from verilog-ethernet, primarily for the
# sake of adding debugging and bringup tools and for compatibility with the
# rest of the LiteX ecosystem.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_baser.rx import PCSRX
from liteeth.phy.pcs_baser.tx import PCSTX

# PCS ----------------------------------------------------------------------------------------------

class PCS(LiteXModule):
    """10GBASE-R PCS

    XGMII (SDR) to transceiver 64b+2b data+header, wrapping both Tx and Rx directions.
    """
    def __init__(self, dw=64, hdr_width=2, prbs31_enable=False, bitslip_high_cycles=1,
                 bitslip_low_cycles=8, count_125us=195, with_pipelining=False):
        self.xgmii_txd           = Signal(dw)
        self.xgmii_txc           = Signal(dw//8)
        self.xgmii_rxd           = Signal(dw)
        self.xgmii_rxc           = Signal(dw//8)

        self.serdes_tx_data      = Signal(dw)
        self.serdes_tx_hdr       = Signal(hdr_width)
        self.serdes_rx_data      = Signal(dw)
        self.serdes_rx_hdr       = Signal(hdr_width)
        self.serdes_rx_bitslip   = Signal()
        self.serdes_rx_reset_req = Signal()

        # Status outputs
        self.tx_bad_block        = Signal()
        self.rx_error_count      = Signal(7)
        self.rx_bad_block        = Signal()
        self.rx_sequence_error   = Signal()
        self.rx_block_lock       = Signal()
        self.rx_high_ber         = Signal()
        self.rx_status           = Signal()

        # Configuration inputs
        self.cfg_tx_prbs31_enable = Signal()
        self.cfg_rx_prbs31_enable = Signal()

        # Transmit path, XGMII -> PMA

        self.tx = tx = ClockDomainsRenamer("eth_tx")(PCSTX(
            dw            = dw,
            hdr_width     = hdr_width,
            prbs31_enable = prbs31_enable,
        ))

        self.comb += [
            tx.xgmii_txd.eq(self.xgmii_txd),
            tx.xgmii_txc.eq(self.xgmii_txc),
            self.serdes_tx_data.eq(tx.serdes_tx_data),
            self.serdes_tx_hdr.eq(tx.serdes_tx_hdr),
            self.tx_bad_block.eq(tx.tx_bad_block),
            tx.cfg_tx_prbs31_enable.eq(self.cfg_tx_prbs31_enable),
        ]

        # Receive path, PMA -> XGMII

        self.rx = rx = ClockDomainsRenamer("eth_rx")(PCSRX(
            dw                  = dw,
            hdr_width           = hdr_width,
            prbs31_enable       = prbs31_enable,
            bitslip_high_cycles = bitslip_high_cycles,
            bitslip_low_cycles  = bitslip_low_cycles,
            count_125us         = count_125us,
            with_pipelining     = with_pipelining,
        ))

        self.comb += [
            rx.serdes_rx_data.eq(self.serdes_rx_data),
            rx.serdes_rx_hdr.eq(self.serdes_rx_hdr),
            self.serdes_rx_bitslip.eq(rx.serdes_rx_bitslip),
            self.serdes_rx_reset_req.eq(rx.serdes_rx_reset_req),
            self.xgmii_rxd.eq(rx.xgmii_rxd),
            self.xgmii_rxc.eq(rx.xgmii_rxc),
            self.rx_error_count.eq(rx.rx_error_count),
            self.rx_bad_block.eq(rx.rx_bad_block),
            self.rx_sequence_error.eq(rx.rx_sequence_error),
            self.rx_block_lock.eq(rx.rx_block_lock),
            self.rx_high_ber.eq(rx.rx_high_ber),
            self.rx_status.eq(rx.rx_status),
            rx.cfg_rx_prbs31_enable.eq(self.cfg_rx_prbs31_enable),
        ]
