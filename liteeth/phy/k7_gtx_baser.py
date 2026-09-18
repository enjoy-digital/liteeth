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
from liteiclink.serdes.gtx_7series import GTXQuadPLL
from liteeth.phy.pcs_baser import PCS
from liteeth.phy.pma_baser.gtx_7series import PMA_K7_GTX_10G_BASER, PMA_K7_GTX_5G_BASER

# K7_GTX_10G_BASER ---------------------------------------------------------------------------------

class K7_GTX_10G_BASER(LiteXModule):
    """10GBASE-R via a Kintex-7 GTX transceiver.

    Data path:
        sink/source: LiteX stream endpoints, 64 bits wide
        LiteEthPHYXGMII: adapts to the 64-bit SDR form of XGMII
        PCS: 64b/66b coding, scrambling, block sync, BER monitor
        PMA: GTX transceiver wrapper
        data_pads: serdes pads
    """
    dw          = 64
    linerate    = 10.3125e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64

    # Overridden in the 5G subclass.
    transceiver = (GTXQuadPLL, PMA_K7_GTX_10G_BASER)

    def __init__(self, qpll, data_pads, sys_clk_freq, with_csr=True,
        rx_polarity=0, tx_polarity=0, prbs_errors_width=32):

        self.sink   = stream.Endpoint(eth_phy_description(self.dw))
        self.source = stream.Endpoint(eth_phy_description(self.dw))

        self.link_up = Signal()

        # # #

        # PMA (Clause 51) ---------------------------------------------------------------------------
        _, pma_cls = self.transceiver
        self.pma = pma = pma_cls(
            qpll         = qpll,
            data_pads    = data_pads,
            sys_clk_freq = sys_clk_freq,
            tx_polarity  = tx_polarity,
            rx_polarity  = rx_polarity,
        )

        # The transceiver owns the user clock domains; alias them the way the UltraScale+ PHYs do.
        self.cd_eth_tx = pma.cd_eth_tx
        self.cd_eth_rx = pma.cd_eth_rx

        # Exposed for clock constraints.
        self.txoutclk = pma.txoutclk
        self.rxoutclk = pma.rxoutclk

        self.reset    = Signal()
        self.loopback = Signal(3)

        self.comb += [
            pma.reset.eq(self.reset),
            pma.loopback.eq(self.loopback),
        ]

        # PCS ---------------------------------------------------------------------------------------
        # The gearbox is gapped (see the class docstring), so the PCS may only advance on the cycles
        # a block moves. The PCS itself knows nothing of this: CEInserter adds an enable per clock
        # domain from outside, reaching every register the PCS clocks in eth_tx and eth_rx.
        self.pcs = pcs = CEInserter(["eth_tx", "eth_rx"])(PCS(
            dw            = self.dw,
            count_125us   = int(125e-6*self.linerate/66),
            prbs31_enable = True,
        ))

        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()

        self.comb += [
            pcs.cfg_tx_prbs31_enable.eq(self.tx_prbs31_enable),
            pcs.cfg_rx_prbs31_enable.eq(self.rx_prbs31_enable),

            # Advance the PCS only on the cycles the gearbox actually carries a block.
            pcs.ce_eth_tx.eq(pma.tx_ce),
            pcs.ce_eth_rx.eq(pma.rx_ce),

            pma.tx_data.eq(pcs.serdes_tx_data),
            pma.tx_header.eq(pcs.serdes_tx_hdr),
            pcs.serdes_rx_data.eq(pma.rx_data),
            pcs.serdes_rx_hdr.eq(pma.rx_header),
            pma.rx_slip.eq(pcs.serdes_rx_bitslip),
            # Lets the PCS ask for a fresh CDR lock when it cannot reach block sync.
            pma.rx_reset_req.eq(pcs.serdes_rx_reset_req),

            self.link_up.eq(pcs.rx_status),
        ]

        # PRBS31 error counter, matching the convention of the other BASE-R PHYs.
        self.rx_prbs_pause  = Signal()
        self.rx_prbs_errors = Signal(prbs_errors_width)

        prbs_pause  = Signal()
        prbs_errors = Signal(prbs_errors_width)
        prbs_next   = Signal(prbs_errors_width + 1)

        self.specials += MultiReg(self.rx_prbs_pause, prbs_pause, "eth_rx")

        self.comb += prbs_next.eq(prbs_errors + pcs.rx_error_count)
        # Counts per block, so it only advances on the cycles a block arrives.
        self.sync.eth_rx += If(~self.rx_prbs31_enable,
            prbs_errors.eq(0),
        ).Elif(~prbs_pause & pma.rx_ce,
            # Saturate rather than wrap
            If(prbs_next[prbs_errors_width],
                prbs_errors.eq(2**prbs_errors_width - 1),
            ).Else(
                prbs_errors.eq(prbs_next[:prbs_errors_width]),
            ),
        )

        self.specials += MultiReg(prbs_errors, self.rx_prbs_errors)

        # XGMII -------------------------------------------------------------------------------------
        self.xgmii_pads = xgmii_pads = LiteEthPHYXGMIIPads()

        # CEInserter() provides the .ce that advances these one word per block.
        self.xgmii_tx = ClockDomainsRenamer("eth_tx")(CEInserter()(
            LiteEthPHYXGMIITX(xgmii_pads, self.dw)))
        self.xgmii_rx = ClockDomainsRenamer("eth_rx")(CEInserter()(
            LiteEthPHYXGMIIRX(xgmii_pads, self.dw)))

        # Pipeline the MAC -> XGMII handoff. This domain runs at 161.13MHz at 10GBASE-R and the
        # MAC's TX CRC into the XGMII adapter is the critical path. Costs one cycle of latency.
        # Must live in eth_tx like xgmii_tx: a bare stream.Buffer lands in sys and silently creates
        # an unsynchronised sys <-> eth_tx crossing.
        self.tx_pipe = tx_pipe = ClockDomainsRenamer("eth_tx")(
            stream.Buffer(eth_phy_description(self.dw)))

        self.comb += [
            self.sink.connect(tx_pipe.sink),
            tx_pipe.source.connect(self.xgmii_tx.sink, omit={"valid", "ready"}),
            # Qualify the handshake with the enable, or the MAC hands over a word on the pause
            # cycle and it is silently dropped.
            self.xgmii_tx.sink.valid.eq(tx_pipe.source.valid & pma.tx_ce),
            tx_pipe.source.ready.eq(self.xgmii_tx.sink.ready & pma.tx_ce),

            self.xgmii_rx.source.connect(self.source, omit={"valid", "ready"}),
            self.xgmii_rx.source.ready.eq(self.source.ready),

            pcs.xgmii_txd.eq(xgmii_pads.tx_data),
            pcs.xgmii_txc.eq(xgmii_pads.tx_ctl),
            xgmii_pads.rx_data.eq(pcs.xgmii_rxd),
            xgmii_pads.rx_ctl.eq(pcs.xgmii_rxc),

            self.xgmii_tx.ce.eq(pma.tx_ce),
            self.xgmii_rx.ce.eq(pma.rx_ce),
        ]

        # Delay the MAC-facing valid by one cycle to match the enable, as the A7 BASE-R PHY does.
        rx_mac_ce = Signal()
        self.sync.eth_rx += rx_mac_ce.eq(pma.rx_ce)
        self.comb += self.source.valid.eq(self.xgmii_rx.source.valid & rx_mac_ce)

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

        self._control = CSRStorage(description="PHY control.", fields=[
            CSRField("loopback", size=3, description=
                     "Transceiver loopback (UG476 ch 2): 0 off, 1 near-end PCS, "
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
            " times the block rate, linerate/66, times 66 bits per block.")
        self.comb += self._rx_prbs_errors.status.eq(self.rx_prbs_errors)

    def add_timing_constraints(self, platform):
        pass


# K7_GTX_5G_BASER ----------------------------------------------------------------------------------

class K7_GTX_5G_BASER(K7_GTX_10G_BASER):
    linerate    = 5.15625e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64

    transceiver = (GTXQuadPLL, PMA_K7_GTX_5G_BASER)
