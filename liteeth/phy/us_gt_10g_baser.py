#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from litex.gen import *

from migen.genlib.cdc import MultiReg

from litex.soc.interconnect.csr import CSRField, CSRStatus, CSRStorage

from liteeth.common import *
from liteeth.phy.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads
from liteiclink.serdes.gty_ultrascale import GTYQuadPLL
from liteiclink.serdes.gth4_ultrascale import GTH4QuadPLL
from liteeth.phy.pcs_10g import PCS
from liteeth.phy.pma_10g import PMA_USP_GTY_10G_BASER, PMA_USP_GTH_10G_BASER


class USP_GTY_10G_BASER(LiteXModule):
    """10GBASE-R via UltraScale+ GTY transceiver

    Data path:
        sink/source: LiteX stream endpoints, must be 64 bits wide
        LiteEthPHYXGMII: adapts to 64-bit wide SDR form of XGMII
        PCS: 64b/66b coding, scrambling, block sync, BER monitor
        PMA: GT transceiver wrapper
        data_pads: serdes pads

    This PHY handles its own clocking. Currently only a reference clock of
    156.25e6 is verified, but other clocks may work.
    """
    dw          = 64
    linerate    = 10.3125e9
    rx_clk_freq = 156.25e6
    tx_clk_freq = 156.25e6

    # Overridden in the subclass for GTH
    transceiver = (GTYQuadPLL, PMA_USP_GTY_10G_BASER)

    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=156.25e6,
        with_csr=True, rx_polarity=0, tx_polarity=0, refclk_from_fabric=False,
        prbs_errors_width=32):
        self.sink    = stream.Endpoint(eth_phy_description(self.dw))
        self.source  = stream.Endpoint(eth_phy_description(self.dw))

        self.link_up = Signal()

        # Exposed for easy setting of clock constraints
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        self.reset = Signal()

        # Reference clock ---------------------------------------------------------------------------
        refclk = Signal()
        if isinstance(refclk_or_clk_pads, Signal):
            self.comb += refclk.eq(refclk_or_clk_pads)
        elif refclk_from_fabric:
            self.comb += refclk.eq(refclk_or_clk_pads)
        else:
            self.refclk_buf = Instance("IBUFDS_GTE4",
                i_CEB = 0,
                i_I   = refclk_or_clk_pads.p,
                i_IB  = refclk_or_clk_pads.n,
                o_O   = refclk,
                p_REFCLK_HROW_CK_SEL = 0b00,
            )

        pll_cls, pma_cls = self.transceiver

        self.pll = pll = pll_cls(refclk, refclk_freq, self.linerate)

        # PMA (Clause 51) ---------------------------------------------------------------------------
        self.pma = pma = pma_cls(
            pll          = pll,
            data_pads    = data_pads,
            sys_clk_freq = sys_clk_freq,
            tx_polarity  = tx_polarity,
            rx_polarity  = rx_polarity,
        )

        self.cd_eth_tx = pma.cd_eth_tx
        self.cd_eth_rx = pma.cd_eth_rx
        self.comb += [
            self.txoutclk.eq(pma.txoutclk),
            self.rxoutclk.eq(pma.rxoutclk),
        ]

        self.loopback = Signal(3)

        self.comb += [
            pma.tx_init.restart.eq(self.reset),
            pma.rx_init.restart.eq(self.reset),
            pma.loopback.eq(self.loopback),
        ]

        # PCS

        self.pcs = pcs = PCS(
            dw            = self.dw,
            count_125us   = int(125e-6*self.rx_clk_freq),
            prbs31_enable = True,
        )

        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()

        self.comb += [
            pcs.cfg_tx_prbs31_enable.eq(self.tx_prbs31_enable),
            pcs.cfg_rx_prbs31_enable.eq(self.rx_prbs31_enable),
        ]

        # PRBS31 error counter, tries to match the convention of other LiteEth PHYs while
        # staying lightweight.
        self.rx_prbs_pause  = Signal()
        self.rx_prbs_errors = Signal(prbs_errors_width)

        prbs_pause  = Signal()
        prbs_errors = Signal(prbs_errors_width)
        prbs_next   = Signal(prbs_errors_width + 1)

        self.specials += MultiReg(self.rx_prbs_pause, prbs_pause, "eth_rx")

        self.comb += prbs_next.eq(prbs_errors + pcs.rx_error_count)
        self.sync.eth_rx += If(~self.rx_prbs31_enable,
            prbs_errors.eq(0),
        ).Elif(~prbs_pause,
            # Saturate rather than wrap
            If(prbs_next[prbs_errors_width],
                prbs_errors.eq(2**prbs_errors_width - 1),
            ).Else(
                prbs_errors.eq(prbs_next[:prbs_errors_width]),
            ),
        )

        self.specials += MultiReg(prbs_errors, self.rx_prbs_errors)

        self.comb += [
            pma.tx_data.eq(pcs.serdes_tx_data),
            pma.tx_header.eq(pcs.serdes_tx_hdr),
            pcs.serdes_rx_data.eq(pma.rx_data),
            pcs.serdes_rx_hdr.eq(pma.rx_header),
            pma.rx_slip.eq(pcs.serdes_rx_bitslip),

            self.link_up.eq(pcs.rx_status),
        ]

        # XGMII

        self.xgmii_pads = xgmii_pads = LiteEthPHYXGMIIPads()

        self.xgmii_tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYXGMIITX(xgmii_pads, self.dw))
        self.xgmii_rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYXGMIIRX(xgmii_pads, self.dw))

        self.comb += [
            self.sink.connect(self.xgmii_tx.sink),
            self.xgmii_rx.source.connect(self.source),

            pcs.xgmii_txd.eq(xgmii_pads.tx_data),
            pcs.xgmii_txc.eq(xgmii_pads.tx_ctl),
            xgmii_pads.rx_data.eq(pcs.xgmii_rxd),
            xgmii_pads.rx_ctl.eq(pcs.xgmii_rxc),
        ]


        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

        self._control = CSRStorage(description="PHY control.", fields=[
            CSRField("loopback", size=3, description=
                     "Transceiver loopback (UG578 ch 2): 0 off, 1 near-end PCS, "
                     "2 near-end PMA, 4 far-end PMA, 6 far-end PCS"),
            CSRField("tx_prbs31_enable", size=1, description=
                "Transmit PRBS31 test pattern (49.2.8)"),
            CSRField("rx_prbs31_enable", size=1, description=
                "Check received stream against PRBS31 (49.2.12)"),
            CSRField("prbs_pause", size=1, description=
                "Freeze the PRBS31 error counter so that it can be read coherently"),
        ])

        self.specials += [
            MultiReg(self._control.fields.loopback,         self.loopback),
            MultiReg(self._control.fields.tx_prbs31_enable, self.tx_prbs31_enable, "eth_tx"),
            MultiReg(self._control.fields.rx_prbs31_enable, self.rx_prbs31_enable, "eth_rx"),
        ]

        self._status = CSRStatus(description="PHY status.", fields=[
            CSRField("block_lock", size=1, description=
                "Block synchronisation acquired (49.2.9, Figure 49-12)"),
            CSRField("high_ber", size=1, description=
                "Bit error ratio worse than 1e-4 (Figure 49-13)"),
            CSRField("link_up", size=1, description=
                "PCS_status: block lock held and no high BER (49.2.14.1)"),
            CSRField("error_count", size=7, description=
                "PRBS31 bit errors in the last block, valid in receive test-pattern mode"),
            CSRField("block_lock_lost", size=1, description=
                "Block lock has been absent at some point since this register was last read"),
            CSRField("high_ber_latched", size=1, description=
                "high_ber has been asserted at some point since this register was last read"),
        ])

        self.specials += [
            MultiReg(self.pcs.rx_block_lock,   self._status.fields.block_lock),
            MultiReg(self.pcs.rx_high_ber,     self._status.fields.high_ber),
            MultiReg(self.link_up,             self._status.fields.link_up),
            MultiReg(self.pcs.rx_error_count,  self._status.fields.error_count),
        ]

        block_lock_lost  = Signal()
        high_ber_latched = Signal()
        # Reset on read
        self.sync += If(self._status.we,
            block_lock_lost.eq( ~self._status.fields.block_lock),
            high_ber_latched.eq(self._status.fields.high_ber),
        ).Else(
            If(~self._status.fields.block_lock, block_lock_lost.eq(1)),
            If(self._status.fields.high_ber,    high_ber_latched.eq(1)),
        )
        self.comb += [
            self._status.fields.block_lock_lost.eq( block_lock_lost),
            self._status.fields.high_ber_latched.eq(high_ber_latched),
            self.rx_prbs_pause.eq(self._control.fields.prbs_pause),
        ]

        self._rx_prbs_errors = CSRStatus(len(self.rx_prbs_errors), description=
            "PRBS31 bit errors since the test was last enabled, saturating rather than wrapping."
            " Set prbs_pause before reading: the counter lives in the receive clock domain and is"
            " only stable while paused. For a bit error ratio, the denominator is the elapsed time"
            " times the receive clock frequency times 66 bits per block.")
        self.comb += self._rx_prbs_errors.status.eq(self.rx_prbs_errors)


class USP_GTH_10G_BASER(USP_GTY_10G_BASER):
    """10GBASE-R via UltraScale+ GTH transceiver"""
    transceiver = (GTH4QuadPLL, PMA_USP_GTH_10G_BASER)
