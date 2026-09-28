#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteiclink.serdes.gtx_7series import GTXQuadPLL

from liteeth.common import *
from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY
from liteeth.phy.parallel.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads
from liteeth.phy.serial.baser.pcs import PCS
from liteeth.phy.serial.baser.pma.gtx_7series import PMA_K7_GTX_10G_BASER, PMA_K7_GTX_5G_BASER

# K7_GTX_10G_BASER ---------------------------------------------------------------------------------

class K7_GTX_10G_BASER(LiteEthBASERPHY):
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
    loopback_description = (
        "Transceiver loopback (UG476 ch 2): 0 off, 1 near-end PCS, "
        "2 near-end PMA, 4 far-end PMA, 6 far-end PCS"
    )

    # Overridden in the 5G subclass.
    transceiver = (GTXQuadPLL, PMA_K7_GTX_10G_BASER)

    def __init__(self, qpll, data_pads, sys_clk_freq, with_csr=True,
        rx_polarity=0, tx_polarity=0, prbs_errors_width=32, pll_master=True):

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
            pll_master   = pll_master,
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

        self.add_prbs_counter(prbs_errors_width, rx_ce=pma.rx_ce)

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

    def add_timing_constraints(self, platform):
        pass


# K7_GTX_5G_BASER ----------------------------------------------------------------------------------

class K7_GTX_5G_BASER(K7_GTX_10G_BASER):
    linerate    = 5.15625e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64

    transceiver = (GTXQuadPLL, PMA_K7_GTX_5G_BASER)
