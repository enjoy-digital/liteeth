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
from liteeth.phy.serial.gtp_7series import *
from liteeth.phy.serial.basex.pcs import *

from liteeth.phy.serial.basex.pma.gtp_7series import PMA_A7_GTP_BASEX

# A7_1000BASEX PHY ---------------------------------------------------------------------------------

class A7_1000BASEX(LiteXModule):
    dw          = 8
    linerate    = 1.25e9
    rx_clk_freq = 125e6
    tx_clk_freq = 125e6
    def __init__(self, qpll_channel, data_pads, sys_clk_freq, with_csr=True,
        # PCS Parameters.
        pcs_kwargs       = None,
        with_pcs_buffers = False,

        # TX Parameters.
        tx_cm_type     = "PLL",
        tx_cm_buf_type = "BUFH",
        tx_polarity    = 0,

        # RX Parameters.
        rx_cm_type     = "PLL",
        rx_cm_buf_type = "BUFG",
        rx_polarity    = 0,
    ):
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS(lsb_first=True, **pcs_kwargs)

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
        self.pma = pma = PMA_A7_GTP_BASEX(
            qpll_channel, data_pads, sys_clk_freq,
            linerate       = self.linerate,
            tx_clk_freq    = self.tx_clk_freq,
            rx_clk_freq    = self.rx_clk_freq,
            tx_cm_type     = tx_cm_type,
            tx_cm_buf_type = tx_cm_buf_type,
            rx_cm_type     = rx_cm_type,
            rx_cm_buf_type = rx_cm_buf_type,
            rx_polarity    = rx_polarity,
            tx_polarity    = tx_polarity,
            with_channel   = False,
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
            "gtp_params", "tx_cm", "rx_cm", "tx_init", "rx_init",
        ):
            object.__setattr__(self, name, getattr(pma, name))
        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

    def do_finalize(self):
        # Keep this hook for targets that customize gtp_params during finalization.
        self.specials += Instance("GTPE2_CHANNEL", **self.gtp_params)

# A7_2500BASEX PHY ---------------------------------------------------------------------------------

class A7_2500BASEX(A7_1000BASEX):
    linerate    = 3.125e9
    rx_clk_freq = 312.5e6
    tx_clk_freq = 312.5e6
