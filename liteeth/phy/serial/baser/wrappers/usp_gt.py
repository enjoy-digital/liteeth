#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

import math

from migen import *
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *

from liteiclink.serdes.gth4_ultrascale import GTH4QuadPLL
from liteiclink.serdes.gty_ultrascale import GTYQuadPLL

from liteeth.common import *
from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY
from liteeth.phy.serial.baser.pcs import PCS
from liteeth.phy.serial.baser.pma import (PMA_USP_GTY_10G_BASER, PMA_USP_GTH_10G_BASER,
                                PMA_USP_GTY_5G_BASER, PMA_USP_GTH_5G_BASER, PMA_USP_GTY_25G_BASER)
from liteeth.phy.parallel.xgmii import LiteEthPHYXGMIIRX, LiteEthPHYXGMIITX, LiteEthPHYXGMIIPads

# UltraScale+ BASE-R PHY ---------------------------------------------------------------------------

class USP_GTY_10G_BASER(LiteEthBASERPHY):
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
    loopback_description = (
        "Transceiver loopback (UG578 ch 2): 0 off, 1 near-end PCS, "
        "2 near-end PMA, 4 far-end PMA, 6 far-end PCS"
    )
    prbs_rate_description = "receive clock frequency times 66 bits per block."

    # Overridden in the subclasses for GTH and for 5GBASE-R
    transceiver = (GTYQuadPLL, PMA_USP_GTY_10G_BASER)
    pll_kwargs  = {}

    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=156.25e6,
        with_csr=True, rx_polarity=0, tx_polarity=0, refclk_from_fabric=False,
        prbs_errors_width=32, pll=None, pll_master=True):
        if pll is None and not pll_master:
            raise ValueError("pll_master=False requires an external PLL.")

        self.sink    = stream.Endpoint(eth_phy_description(self.dw))
        self.source  = stream.Endpoint(eth_phy_description(self.dw))

        self.link_up = Signal()

        # Exposed for easy setting of clock constraints
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        self.reset = Signal()

        # Reference clock / PLL --------------------------------------------------------------------
        pll_cls, pma_cls = self.transceiver
        if pll is None:
            refclk = Signal()
            if isinstance(refclk_or_clk_pads, Signal) or refclk_from_fabric:
                self.comb += refclk.eq(refclk_or_clk_pads)
            else:
                self.refclk_buf = Instance("IBUFDS_GTE4",
                    i_CEB = 0,
                    i_I   = refclk_or_clk_pads.p,
                    i_IB  = refclk_or_clk_pads.n,
                    o_O   = refclk,
                    p_REFCLK_HROW_CK_SEL = 0b00,
                )
            self.pll = pll = pll_cls(refclk, refclk_freq, self.linerate,
                refclk_from_fabric=refclk_from_fabric, **self.pll_kwargs)
        else:
            if not isinstance(pll, pll_cls) or not math.isclose(pll.config["linerate"], self.linerate, rel_tol=1e-12):
                raise ValueError(f"External PLL must provide {self.linerate/1e9:g} Gb/s using {pll_cls.__name__}.")
            if "qpll" in self.pll_kwargs:
                if pll.config["qpll"] != self.pll_kwargs["qpll"]:
                    raise ValueError(f"External PLL must use {self.pll_kwargs['qpll']}.")
                for name, value in self.pll_kwargs["qpll_params"].items():
                    if pll.gty_params["p_" + name] != value:
                        raise ValueError(f"External PLL has incompatible {name} tuning.")
            # The parent owns a shared common primitive and its reference-clock routing.
            # Bypass automatic submodule registration to avoid instantiating the PLL twice.
            object.__setattr__(self, "pll", pll)

        # PMA (Clause 51) ---------------------------------------------------------------------------
        self.pma = pma = pma_cls(
            pll          = pll,
            data_pads    = data_pads,
            sys_clk_freq = sys_clk_freq,
            tx_polarity  = tx_polarity,
            rx_polarity  = rx_polarity,
            pll_master   = pll_master,
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
            pma.loopback.eq(self.loopback),
        ]

        # PCS

        pipelined = eth_needs_pipelining(self)

        self.pcs = pcs = PCS(
            dw              = self.dw,
            count_125us     = int(125e-6*self.rx_clk_freq),
            prbs31_enable   = True,
            with_pipelining = pipelined,
        )

        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()

        self.comb += [
            pcs.cfg_tx_prbs31_enable.eq(self.tx_prbs31_enable),
            pcs.cfg_rx_prbs31_enable.eq(self.rx_prbs31_enable),
        ]

        self.add_prbs_counter(prbs_errors_width)

        self.comb += [
            pma.tx_data.eq(pcs.serdes_tx_data),
            pma.tx_header.eq(pcs.serdes_tx_hdr),
            pcs.serdes_rx_data.eq(pma.rx_data),
            pcs.serdes_rx_hdr.eq(pma.rx_header),
            pma.rx_slip.eq(pcs.serdes_rx_bitslip),

            self.link_up.eq(pcs.rx_status),
        ]

        # Lets the PCS ask for a fresh CDR lock when it cannot reach block sync.
        ps_restart = PulseSynchronizer("eth_rx", "sys")
        self.submodules += ps_restart
        self.comb += [
            ps_restart.i.eq(pcs.serdes_rx_reset_req),
            pma.rx_init.restart.eq(self.reset | ps_restart.o),
        ]

        # XGMII

        self.xgmii_pads = xgmii_pads = LiteEthPHYXGMIIPads()

        self.xgmii_tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYXGMIITX(xgmii_pads, self.dw))
        self.xgmii_rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYXGMIIRX(xgmii_pads, self.dw))

        # Registers the MAC datapath paths into the XGMII adapter.
        if pipelined:
            self.tx_buffer = ClockDomainsRenamer("eth_tx")(
                stream.Buffer(eth_phy_description(self.dw), pipe_valid=True, pipe_ready=True))
            self.comb += [
                self.sink.connect(self.tx_buffer.sink),
                self.tx_buffer.source.connect(self.xgmii_tx.sink),
            ]
        else:
            self.comb += self.sink.connect(self.xgmii_tx.sink)

        self.comb += self.xgmii_rx.source.connect(self.source)

        # XGMII has no handshake, so registering it is latency-only. Splits the MAC-to-encoder
        # and PCS-to-MAC paths, which do not close at 390.625 MHz otherwise.
        xgmii_tx_conn = [
            pcs.xgmii_txd.eq(xgmii_pads.tx_data),
            pcs.xgmii_txc.eq(xgmii_pads.tx_ctl),
        ]
        xgmii_rx_conn = [
            xgmii_pads.rx_data.eq(pcs.xgmii_rxd),
            xgmii_pads.rx_ctl.eq(pcs.xgmii_rxc),
        ]
        if pipelined:
            self.sync.eth_tx += xgmii_tx_conn
            self.sync.eth_rx += xgmii_rx_conn
        else:
            self.comb += xgmii_tx_conn + xgmii_rx_conn


        if with_csr:
            self.add_csr()


class USP_GTH_10G_BASER(USP_GTY_10G_BASER):
    """10GBASE-R via UltraScale+ GTH transceiver"""
    transceiver = (GTH4QuadPLL, PMA_USP_GTH_10G_BASER)


class USP_GTY_5G_BASER(USP_GTY_10G_BASER):
    """5GBASE-R via UltraScale+ GTY transceiver"""
    linerate    = 5.15625e9
    rx_clk_freq = linerate/66   # one 66-bit block per user clock: 78.125 MHz
    tx_clk_freq = linerate/66

    transceiver = (GTYQuadPLL, PMA_USP_GTY_5G_BASER)


class USP_GTH_5G_BASER(USP_GTH_10G_BASER):
    """5GBASE-R via UltraScale+ GTH transceiver"""
    linerate    = 5.15625e9
    rx_clk_freq = linerate/66   # one 66-bit block per user clock: 78.125 MHz
    tx_clk_freq = linerate/66

    transceiver = (GTH4QuadPLL, PMA_USP_GTH_5G_BASER)


class USP_GTY_25G_BASER(USP_GTY_10G_BASER):
    """25GBASE-R via UltraScale+ GTY transceiver

    The QPLL VCO runs at 12.890625 GHz full-rate, which from a 156.25 MHz reference is
    N = 82.5, so the QPLL runs fractional-N. A 161.1328125 MHz reference gives integer-N and is
    preferable on jitter grounds. User clocks are 390.625 MHz, which the attached MAC datapath
    must also close timing at.
    """
    linerate    = 25.78125e9
    rx_clk_freq = linerate/66   # one 66-bit block per user clock: 390.625 MHz
    tx_clk_freq = linerate/66

    transceiver = (GTYQuadPLL, PMA_USP_GTY_25G_BASER)
    pll_kwargs  = dict(qpll="qpll0", qpll_params=PMA_USP_GTY_25G_BASER.qpll_params)
