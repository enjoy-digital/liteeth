#
# This file is part of LiteEth.
#
# Ported from A7_5000BASER on the feature/a7-5000baser # branch by # enjoy-digital. That PHY 
# implementation drove a vendored Verilog PCS. This is adapted to a pure-LiteX BASE-R PCS.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from litex.gen import *

from migen.genlib.cdc import MultiReg

from litex.soc.interconnect.csr import CSRField, CSRStatus, CSRStorage

from liteeth.common import *
from liteeth.phy.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads
from liteeth.phy.pcs_baser import PCS
from liteeth.phy.pma_baser.gtp_7series import PMA_A7_GTP_5G_BASER

# A7_GTP_5G_BASER ----------------------------------------------------------------------------------

class A7_GTP_5G_BASER(LiteXModule):
    """5GBASE-R via an Artix-7 GTP transceiver.

    Data path:
        sink/source: LiteX stream endpoints, 64 bits wide
        LiteEthPHYXGMII: adapts to the 64-bit SDR form of XGMII
        PCS: 64b/66b coding, scrambling, block sync, BER monitor
        PMA: GTP transceiver wrapper
        data_pads: serdes pads
    """
    dw          = 64
    linerate    = 5.15625e9
    tx_clk_freq = linerate/32
    rx_clk_freq = linerate/32

    def __init__(self, qpll_channel, data_pads, sys_clk_freq, refclk_freq, with_csr=True,
        rx_polarity=0, tx_polarity=0, with_prbs=True, prbs_errors_width=32):
        self.with_prbs = with_prbs

        self.sink   = stream.Endpoint(eth_phy_description(self.dw))
        self.source = stream.Endpoint(eth_phy_description(self.dw))

        self.link_up = Signal()

        # # #

        # PMA ---------------------------------------------------------------------------------------
        self.pma = pma = PMA_A7_GTP_5G_BASER(
            qpll_channel = qpll_channel,
            data_pads    = data_pads,
            sys_clk_freq = sys_clk_freq,
            refclk_freq  = refclk_freq,
            tx_polarity  = tx_polarity,
            rx_polarity  = rx_polarity,
        )

        # The transceiver owns the user clock domains.
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
        self.pcs = pcs = CEInserter(["eth_tx", "eth_rx"])(PCS(
            dw              = self.dw,
            count_125us     = int(125e-6*self.linerate/66),
            prbs31_enable   = with_prbs,
            with_pipelining = True,
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

        if with_prbs:
            # PRBS31 error counter
            self.rx_prbs_pause  = Signal()
            self.rx_prbs_errors = Signal(prbs_errors_width)

            prbs_pause  = Signal()
            prbs_errors = Signal(prbs_errors_width)
            prbs_next   = Signal(prbs_errors_width + 1)

            self.specials += MultiReg(self.rx_prbs_pause, prbs_pause, "eth_rx")

            self.comb += prbs_next.eq(prbs_errors + pcs.rx_error_count)
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

        # Pipeline the MAC -> XGMII handoff
        self.tx_pipe = tx_pipe = ClockDomainsRenamer("eth_tx")(
            stream.Buffer(eth_phy_description(self.dw)))

        # And the XGMII -> MAC handoff
        self.rx_pipe = rx_pipe = ClockDomainsRenamer("eth_rx")(
            stream.Buffer(eth_phy_description(self.dw)))
        self.comb += rx_pipe.source.connect(self.source)

        self.comb += [
            self.sink.connect(tx_pipe.sink),
            tx_pipe.source.connect(self.xgmii_tx.sink, omit={"valid", "ready"}),
            self.xgmii_tx.sink.valid.eq(tx_pipe.source.valid & pma.tx_ce),
            tx_pipe.source.ready.eq(self.xgmii_tx.sink.ready & pma.tx_ce),

            self.xgmii_rx.source.connect(rx_pipe.sink, omit={"valid", "ready"}),
            self.xgmii_rx.source.ready.eq(rx_pipe.sink.ready),

            pcs.xgmii_txd.eq(xgmii_pads.tx_data),
            pcs.xgmii_txc.eq(xgmii_pads.tx_ctl),
            xgmii_pads.rx_data.eq(pcs.xgmii_rxd),
            xgmii_pads.rx_ctl.eq(pcs.xgmii_rxc),

            self.xgmii_tx.ce.eq(pma.tx_ce),
            self.xgmii_rx.ce.eq(pma.rx_ce),
        ]

        # Delay the MAC-facing valid by one cycle to match the enable.
        rx_mac_ce = Signal()
        self.sync.eth_rx += rx_mac_ce.eq(pma.rx_ce)
        self.comb += rx_pipe.sink.valid.eq(self.xgmii_rx.source.valid & rx_mac_ce)

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

        self._control = CSRStorage(description="PHY control.", fields=[
            CSRField("loopback", size=3, description=
                     "Transceiver loopback (UG482): 0 off, 1 near-end PCS, "
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
        ]

        if not self.with_prbs:
            return
        self.comb += self.rx_prbs_pause.eq(self._control.fields.prbs_pause)
        self._rx_prbs_errors = CSRStatus(len(self.rx_prbs_errors), description=
            "PRBS31 bit errors since the test was last enabled, saturating rather than wrapping."
            " Set prbs_pause before reading: the counter lives in the receive clock domain and is"
            " only stable while paused. For a bit error ratio, the denominator is the elapsed time"
            " times the block rate, linerate/66, times 66 bits per block.")
        self.comb += self._rx_prbs_errors.status.eq(self.rx_prbs_errors)

    def add_timing_constraints(self, platform):
        """Declare TXOUTCLK/RXOUTCLK at linerate/16."""
        period = "%.3f" % (1e9/(self.linerate/16))
        platform.add_platform_command(
            "create_clock -name {txoutclk} -period " + period + " [get_nets {txoutclk}]",
            txoutclk = self.txoutclk)
        platform.add_platform_command(
            "create_clock -name {rxoutclk} -period " + period + " [get_nets {rxoutclk}]",
            rxoutclk = self.rxoutclk)
