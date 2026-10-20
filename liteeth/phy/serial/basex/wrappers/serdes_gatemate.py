#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2020-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Sergey Razumov <cyntem@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *

from litex.soc.cores.clock import S7PLL, S7MMCM

from liteeth.common import *

from liteeth.phy.serial.basex.pcs32 import PCS32
from liteeth.phy.serial.basex.pma.serdes_gatemate import PMA_SERDES_GateMate

# GateMate_1000BASEX PHY ---------------------------------------------------------------------------------

class GateMate_1000BASEX(LiteXModule):
    dw          = 32
    linerate    = 1.25e9
    rx_clk_freq = 31.25e6
    tx_clk_freq = 31.25e6
    def __init__(self, sys_clk_freq,
        ref_clk_freq = 100e6,
        with_csr=True,
        # PCS Parameters.
        pcs_kwargs       = None,
        with_pcs_buffers = True,

        # TX Parameters.
        tx_polarity    = 0,

        # RX Parameters.
        rx_polarity    = 0,
    ):
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS32(lsb_first=True, **pcs_kwargs)

        # Optional pipeline cuts at the MAC boundary ease timing closure when
        # the PCS runs at 312.5MHz. The TBI/autonegotiation path is unchanged.
        if with_pcs_buffers:
            self.tx_pcs_buffer = tx_pcs_buffer = ClockDomainsRenamer("eth_tx")(
                stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
            self.rx_pcs_buffer = rx_pcs_buffer = ClockDomainsRenamer("eth_rx")(
                stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
            self.sink   = tx_pcs_buffer.sink
            self.source = rx_pcs_buffer.source
            self.comb += [
                tx_pcs_buffer.source.connect(pcs.sink),
                pcs.source.connect(rx_pcs_buffer.sink),
            ]
        else:
            self.sink   = pcs.sink
            self.source = pcs.source
        self.link_up = pcs.link_up

        # PMA --------------------------------------------------------------------------------------
        self.pma = pma = PMA_SERDES_GateMate(
            sys_clk_freq   = sys_clk_freq,
            refclk_freq    = ref_clk_freq,
            rx_polarity    = rx_polarity,
            tx_polarity    = tx_polarity,
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
        for name in (
            "cd_eth_tx", "cd_eth_rx",
            "txoutclk", "rxoutclk", "reset",
        ):
            object.__setattr__(self, name, getattr(pma, name))
        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)
