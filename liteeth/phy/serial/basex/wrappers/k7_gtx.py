#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import math

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *

from litex.soc.cores.clock import S7MMCM

from liteiclink.serdes.gtx_7series import GTXChannelPLL, GTXTXInit, GTXRXInit

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import *

from liteeth.phy.serial.basex.pma.gtx_7series import PMA_K7_GTX_BASEX

# K7_1000BASEX PHY ---------------------------------------------------------------------------------

class K7_1000BASEX(LiteXModule):
    dw          = 8
    linerate    = 1.25e9
    rx_clk_freq = 125e6
    tx_clk_freq = 125e6

    supported_refclk_freqs = (200e6,)

    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=200e6, with_csr=True, rx_polarity=0, tx_polarity=0):
        if refclk_freq not in self.supported_refclk_freqs:
            raise ValueError(f"Unsupported reference clock {refclk_freq/1e6:g} MHz for {type(self).__name__}.")
        self.pcs = pcs = PCS(lsb_first=True, eth_tx_clk_freq=self.tx_clk_freq)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up

        # PMA --------------------------------------------------------------------------------------
        self.pma = pma = PMA_K7_GTX_BASEX(
            refclk_or_clk_pads, data_pads, sys_clk_freq,
            linerate    = self.linerate,
            tx_clk_freq = self.tx_clk_freq,
            rx_clk_freq = self.rx_clk_freq,
            refclk_freq = refclk_freq,
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
            "pll", "tx_mmcm", "rx_mmcm", "tx_init", "rx_init",
        ):
            object.__setattr__(self, name, getattr(pma, name))
        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

# K7_2500BASEX PHY ---------------------------------------------------------------------------------

class K7_2500BASEX(K7_1000BASEX):
    linerate    = 3.125e9
    rx_clk_freq = 312.5e6
    tx_clk_freq = 312.5e6

    supported_refclk_freqs = (125e6,)

    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq, refclk_freq=125e6,
        with_csr=True, rx_polarity=0, tx_polarity=0):
        super().__init__(refclk_or_clk_pads, data_pads, sys_clk_freq,
            refclk_freq=refclk_freq, with_csr=with_csr,
            rx_polarity=rx_polarity, tx_polarity=tx_polarity)
