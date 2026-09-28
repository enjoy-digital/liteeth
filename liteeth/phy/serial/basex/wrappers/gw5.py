#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import PCS

from liteeth.phy.serial.basex.pma.gw5_config import _serdes_csr, _serdes_toml
from liteeth.phy.serial.basex.pma.gw5 import GW5SerDes

# GW5 1000BASE-X PHY --------------------------------------------------------------------------------

class GW5_1000BASEX(LiteXModule):
    """GW5AST-138B 1000BASE-X PHY on Q1 lane 0 or 1, using a 100 MHz Q1 REFCLK1.

    The hard SerDes uses a raw 10-bit interface at 125 MHz. LiteEth implements the
    8b/10b PCS and autonegotiation; the SerDes performs comma alignment. The embedded
    CSR configuration initializes the analog/clocking blocks during FPGA configuration.
    Dedicated serial and reference-clock pins are selected by the SerDes configuration,
    as in Gowin's generated GTR12_QUAD wrapper.
    """
    dw          = 8
    linerate    = 1.25e9
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6

    def __init__(self, platform, with_csr=True, pcs_kwargs=None, lane=0):
        if platform.devicename != "GW5AST-138B":
            raise ValueError("GW5_1000BASEX currently supports GW5AST-138B.")
        if lane not in (0, 1):
            raise ValueError("GW5_1000BASEX supports Q1 lanes 0 and 1.")

        self.reset     = Signal()
        self.pll_lock  = Signal()
        self.cdr_lock  = Signal()
        self.aligned   = Signal()
        self.cd_eth_tx = ClockDomain()
        self.cd_eth_rx = ClockDomain()

        # PCS --------------------------------------------------------------------------------------
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS(lsb_first=True, **pcs_kwargs)
        self.link_up = pcs.link_up
        if with_csr:
            self.add_csr()

        # # #

        # MAC Interface ----------------------------------------------------------------------------
        # Register the MAC boundary to keep the 125 MHz PCS paths local.
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

        # SerDes Datapath --------------------------------------------------------------------------
        tx_data  = Signal(10)
        rx_data  = Signal(88)
        rx_valid = Signal()
        self.sync.eth_tx += tx_data.eq(pcs.tbi_tx)
        self.sync.eth_rx += [
            pcs.tbi_rx.eq(rx_data[:10]),
            pcs.tbi_rx_ce.eq(rx_valid),
        ]

        # Clocking / Reset -------------------------------------------------------------------------
        reset = self.reset | ResetSignal("sys")
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset | ~self.pll_lock),
            AsyncResetSynchronizer(self.cd_eth_rx, reset | ~self.cdr_lock),
        ]

        # Raw SerDes -------------------------------------------------------------------------------
        self.serdes = serdes = GW5SerDes(platform, lane=lane)
        self.comb += [
            serdes.reset.eq(reset),
            serdes.tx_data.eq(tx_data),
            rx_data.eq(serdes.rx_data),
            rx_valid.eq(serdes.rx_valid),
            self.cd_eth_tx.clk.eq(serdes.tx_clk),
            self.cd_eth_rx.clk.eq(serdes.rx_clk),
            self.pll_lock.eq(serdes.pll_lock),
            self.cdr_lock.eq(serdes.cdr_lock),
            self.aligned.eq(serdes.aligned),
        ]

    def add_csr(self):
        self._reset = CSRStorage(fields=[
            CSRField("reset", description="PHY reset."),
        ])
        self._status = CSRStatus(fields=[
            CSRField("pll_lock", description="Transmit PLL locked."),
            CSRField("cdr_lock", description="Receive clock recovery locked."),
            CSRField("aligned",  description="Receive comma alignment established."),
            CSRField("link_up",  description="PCS autonegotiation completed."),
        ])
        self.comb += self.reset.eq(self._reset.fields.reset)
        self.specials += MultiReg(Cat(self.pll_lock, self.cdr_lock, self.aligned, self.link_up),
            self._status.status)
