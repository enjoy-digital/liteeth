#
# This file is part of LiteEth.
#
# Copyright (c) 2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2020-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Sergey Razumov <cyntem@gmail.com>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.soc.interconnect.csr import CSRStorage, CSRField

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import PCS
from liteeth.phy.serial.basex.pma.gatemate import GateMateSerDes

# GateMate 1000BASE-X PHY --------------------------------------------------------------------------

class GateMate_1000BASEX(LiteXModule):
    """GateMate 1000BASE-X PHY on the CC_SERDES.

    The SerDes performs the 8b/10b coding and comma alignment and exchanges 4 code-groups per
    31.25 MHz cycle; LiteEth implements the PCS (with Clause 37 autonegotiation) on this 32-bit
    datapath. 1000BASE-X only (no SGMII).
    """
    dw          = 32
    linerate    = 1.25e9
    tx_clk_freq = 31.25e6
    rx_clk_freq = 31.25e6

    def __init__(self, sys_clk_freq, refclk_freq=100e6, with_csr=True,
        # PCS Parameters.
        pcs_kwargs       = None,
        with_pcs_buffers = True,

        # TX Parameters.
        tx_polarity = 0,

        # RX Parameters.
        rx_polarity = 0,
    ):
        # PCS --------------------------------------------------------------------------------------
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS(dw=self.dw, **pcs_kwargs)
        self.link_up = pcs.link_up

        # MAC Interface ----------------------------------------------------------------------------
        # Optionally register the MAC boundary to ease timing closure.
        if with_pcs_buffers:
            self.tx_buffer = tx_buffer = ClockDomainsRenamer("eth_tx")(
                stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
            self.rx_buffer = rx_buffer = ClockDomainsRenamer("eth_rx")(
                stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
            self.sink   = tx_buffer.sink
            self.source = rx_buffer.source
            self.comb += [
                tx_buffer.source.connect(pcs.sink),
                pcs.source.connect(rx_buffer.sink),
            ]
        else:
            self.sink   = pcs.sink
            self.source = pcs.source

        # PMA --------------------------------------------------------------------------------------
        self.pma = pma = GateMateSerDes(
            sys_clk_freq = sys_clk_freq,
            refclk_freq  = refclk_freq,
            with_csr     = with_csr,
            tx_polarity  = tx_polarity,
            rx_polarity  = rx_polarity,
        )
        self.comb += [
            pma.tx_data.eq(pcs.tx.data),
            pma.tx_char_is_k.eq(pcs.tx.char_is_k),
            pcs.rx.data.eq(pma.rx_data),
            pcs.rx.char_is_k.eq(pma.rx_char_is_k),
            pcs.rx.table_err.eq(pma.rx_table_err),
            pcs.rx.disparity_err.eq(pma.rx_disparity_err),
            pma.align.eq(pcs.align),
            pma.restart.eq(pcs.restart),
        ]

        # Preserve public handles without registering the PMA's submodules twice.
        for name in ["cd_eth_tx", "cd_eth_rx", "txoutclk", "rxoutclk", "reset"]:
            object.__setattr__(self, name, getattr(pma, name))

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(fields=[
            CSRField("reset", description="PHY reset."),
        ])
        self.comb += self.reset.eq(self._reset.fields.reset)
