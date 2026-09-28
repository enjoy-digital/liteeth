#
# This file is part of LiteEth.
#
# Ported from A7_5000BASER on enjoy-digital's feature/a7-5000baser branch. That PHY implementation
# drove a vendored Verilog PCS; this one is adapted to the pure-LiteX BASE-R PCS.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.common import *
from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY
from liteeth.phy.parallel.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads
from liteeth.phy.serial.baser.pcs import PCS
from liteeth.phy.serial.baser.pma.gtp_7series import PMA_A7_GTP_5G_BASER

# A7_GTP_5G_BASER ----------------------------------------------------------------------------------

class A7_GTP_5G_BASER(LiteEthBASERPHY):
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
    loopback_description = (
        "Transceiver loopback (UG482): 0 off, 1 near-end PCS, "
        "2 near-end PMA, 4 far-end PMA, 6 far-end PCS"
    )

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
            self.add_prbs_counter(prbs_errors_width, rx_ce=pma.rx_ce)

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

    def add_timing_constraints(self, platform):
        """Declare TXOUTCLK/RXOUTCLK at linerate/16."""
        period = "%.3f" % (1e9/(self.linerate/16))
        platform.add_platform_command(
            "create_clock -name {txoutclk} -period " + period + " [get_nets {txoutclk}]",
            txoutclk = self.txoutclk)
        platform.add_platform_command(
            "create_clock -name {rxoutclk} -period " + period + " [get_nets {rxoutclk}]",
            rxoutclk = self.rxoutclk)
