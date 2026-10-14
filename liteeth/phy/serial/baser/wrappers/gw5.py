#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *
from litex.soc.interconnect.csr import CSRStatus, CSRField

from liteeth.common import *
from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY
from liteeth.phy.parallel.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads
from liteeth.phy.serial.baser.pcs import PCS
from liteeth.phy.serial.baser.pma.gw5 import PMA_GW5_10G_BASER

# GW5_10G_BASER ------------------------------------------------------------------------------------

class GW5_10G_BASER(LiteEthBASERPHY):
    """10GBASE-R via a GW5AST-138B GTR12 transceiver on Q1 lane 0 or 1 (125 MHz Q1 REFCLK0).

    Data path:
        sink/source: LiteX stream endpoints, 64 bits wide
        LiteEthPHYXGMII: adapts to the 64-bit SDR form of XGMII
        PCS: 64b/66b coding, scrambling, block sync, BER monitor
        PMA: soft 66b/64b gearboxes and raw GTR12 SerDes

    The PRBS31 generator/checker is optional (with_prbs) to save timing at 161 MHz.
    """
    dw          = 64
    linerate    = 10.3125e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64
    loopback_description = "Unsupported on GW5 (the SerDes configuration sets the loopback mode)."

    def __init__(self, platform, lane=0, with_csr=True, with_prbs=False, prbs_errors_width=32):
        self.with_prbs = with_prbs

        self.sink   = stream.Endpoint(eth_phy_description(self.dw))
        self.source = stream.Endpoint(eth_phy_description(self.dw))

        self.link_up  = Signal()
        self.reset    = Signal()
        self.loopback = Signal(3)

        # # #

        # PMA --------------------------------------------------------------------------------------
        self.pma = pma = PMA_GW5_10G_BASER(platform, lane=lane)
        self.comb += pma.reset.eq(self.reset)

        # The transceiver owns the user clock domains.
        self.cd_eth_tx = pma.cd_eth_tx
        self.cd_eth_rx = pma.cd_eth_rx

        # PCS --------------------------------------------------------------------------------------
        self.pcs = pcs = CEInserter(["eth_tx", "eth_rx"])(PCS(
            dw              = self.dw,
            count_125us     = int(125e-6*self.linerate/66),
            prbs31_enable   = with_prbs,
            with_pipelining = 2, # Extra receive stages for the 161 MHz GW5 fabric clock.
        ))

        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()

        self.comb += [
            pcs.cfg_tx_prbs31_enable.eq(self.tx_prbs31_enable),
            pcs.cfg_rx_prbs31_enable.eq(self.rx_prbs31_enable),

            # Advance the PCS only on the cycles the gearboxes carry a block.
            pcs.ce_eth_tx.eq(pma.tx_ce),
            pcs.ce_eth_rx.eq(pma.rx_ce),

            pma.tx_data.eq(pcs.serdes_tx_data),
            pma.tx_header.eq(pcs.serdes_tx_hdr),
            pcs.serdes_rx_data.eq(pma.rx_data),
            pcs.serdes_rx_hdr.eq(pma.rx_header),
            pma.rx_slip.eq(pcs.serdes_rx_bitslip & pma.rx_ce),

            self.link_up.eq(pcs.rx_status),
        ]

        if with_prbs:
            self.add_prbs_counter(prbs_errors_width, rx_ce=pma.rx_ce)

        # XGMII ------------------------------------------------------------------------------------
        self.xgmii_pads = xgmii_pads = LiteEthPHYXGMIIPads()

        # CEInserter() provides the .ce that advances these one word per block.
        self.xgmii_tx = ClockDomainsRenamer("eth_tx")(CEInserter()(
            LiteEthPHYXGMIITX(xgmii_pads, self.dw)))
        self.xgmii_rx = ClockDomainsRenamer("eth_rx")(CEInserter()(
            LiteEthPHYXGMIIRX(xgmii_pads, self.dw, with_pipelining=True)))

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

            self.xgmii_tx.ce.eq(pma.tx_ce),
            self.xgmii_rx.ce.eq(pma.rx_ce),
        ]

        # Register the XGMII words between the adapters and the PCS (one block, advancing with the
        # gearbox enables) to keep the 161 MHz paths short. TX starts with idles.
        txd = Signal(64, reset=int("07"*8, 16))
        txc = Signal(8,  reset=0xff)
        rxd = Signal(64)
        rxc = Signal(8)
        self.sync.eth_tx += If(pma.tx_ce,
            txd.eq(xgmii_pads.tx_data),
            txc.eq(xgmii_pads.tx_ctl),
        )
        self.sync.eth_rx += If(pma.rx_ce,
            rxd.eq(pcs.xgmii_rxd),
            rxc.eq(pcs.xgmii_rxc),
        )
        self.comb += [
            pcs.xgmii_txd.eq(txd),
            pcs.xgmii_txc.eq(txc),
            xgmii_pads.rx_data.eq(rxd),
            xgmii_pads.rx_ctl.eq(rxc),
        ]

        # Delay the MAC-facing valid by one cycle to match the enable.
        rx_mac_ce = Signal()
        self.sync.eth_rx += rx_mac_ce.eq(pma.rx_ce)
        self.comb += rx_pipe.sink.valid.eq(self.xgmii_rx.source.valid & rx_mac_ce)

        if with_csr:
            self.add_csr()

    def add_csr(self):
        LiteEthBASERPHY.add_csr(self)
        self._serdes_status = CSRStatus(description="SerDes status.", fields=[
            CSRField("pll_lock", size=1, description="Transmit PLL locked."),
            CSRField("cdr_lock", size=1, description="Receive clock recovery locked."),
        ])
        self.specials += MultiReg(Cat(self.pma.pll_lock, self.pma.cdr_lock),
            self._serdes_status.status)
