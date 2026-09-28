#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import *

from liteeth.phy.serial.basex.pma.gth_usp import PMA_USP_GTH_BASEX

# USP_GTH_1000BASEX PHY ----------------------------------------------------------------------------

class USP_GTH_1000BASEX(LiteXModule):
    # Configured for 200MHz or 156.25MHz transceiver reference clock
    dw          = 8
    linerate    = 1.25e9
    rx_clk_freq = 125e6
    tx_clk_freq = 125e6
    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=200e6, with_csr=True, rx_polarity=0, tx_polarity=0, refclk_from_fabric=False):
        assert refclk_freq in [200e6, 156.25e6]
        self.pcs = pcs = PCS(lsb_first=True, eth_tx_clk_freq=self.tx_clk_freq)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up

        # PMA --------------------------------------------------------------------------------------
        self.pma = pma = PMA_USP_GTH_BASEX(
            refclk_or_clk_pads, data_pads, sys_clk_freq,
            linerate    = self.linerate,
            tx_clk_freq = self.tx_clk_freq,
            rx_clk_freq = self.rx_clk_freq,
            refclk_freq = refclk_freq,
            refclk_from_fabric = refclk_from_fabric,
            rx_polarity = rx_polarity,
            tx_polarity = tx_polarity,
        )
        self.comb += [
            pma.tx_data.eq(pcs.tbi_tx),
            pcs.tbi_rx.eq(pma.rx_data),
            pcs.tbi_rx_ce.eq(pma.rx_valid),
            pma.align.eq(pcs.align),
            pma.restart.eq(pcs.restart),
        ]

        # Preserve public handles without registering the PMA's submodules twice.
        for name in (
            "cd_eth_tx", "cd_eth_rx", "cd_eth_tx_half", "cd_eth_rx_half",
            "txoutclk", "rxoutclk", "reset", "gearbox",
            "pll",
        ):
            object.__setattr__(self, name, getattr(pma, name))
        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

# USP_GTH_2500BASEX PHY ----------------------------------------------------------------------------

class USP_GTH_2500BASEX(USP_GTH_1000BASEX):
    linerate    = 3.125e9
    rx_clk_freq = 312.5e6
    tx_clk_freq = 312.5e6

    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=156.25e6,
        with_csr=True, rx_polarity=0, tx_polarity=0, refclk_from_fabric=False):
        super().__init__(refclk_or_clk_pads, data_pads, sys_clk_freq,
            refclk_freq=refclk_freq, with_csr=with_csr,
            rx_polarity=rx_polarity, tx_polarity=tx_polarity,
            refclk_from_fabric=refclk_from_fabric)
