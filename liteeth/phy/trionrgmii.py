#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Franck Jullien <franck.jullien@collshade.fr>
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Trion Efinix FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import ClkInput, ClkOutput
from litex.build.generic_platform import *
from litex.soc.cores.clock import *

from liteeth.common import *
from liteeth.phy.common import *
from liteeth.phy.titaniumrgmii import LiteEthPHYRGMIITX, LiteEthPHYRGMIIRX

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, platform, clock_pads, with_hw_init_reset, hw_reset_cycles=256, n=0):
        self._reset = CSRStorage()

        # # #

        # Clk Domains.
        # ------------
        self.cd_eth_rx         = ClockDomain()
        self.cd_eth_tx         = ClockDomain()
        self.cd_eth_tx_delayed = ClockDomain(reset_less=True)

        # RX Clk.
        # -------
        self.specials += ClkInput(
            i = clock_pads.rx,
            o = self.cd_eth_rx.clk,
        )

        # TX PLL.
        # -------
        self.pll = pll = TRIONPLL(platform)
        pll.register_clkin(self.cd_eth_rx.clk,    freq=125e6)
        pll.create_clkout(self.cd_eth_rx,         freq=125e6, phase=0,  with_reset=False, is_feedback=True)
        pll.create_clkout(self.cd_eth_tx,         freq=125e6, phase=0,  with_reset=False)
        pll.create_clkout(self.cd_eth_tx_delayed, freq=125e6, phase=45)

        # TX Clk.
        # -------
        self.specials += ClkOutput(
            i = self.cd_eth_tx_delayed.clk,
            o = clock_pads.tx
        )

        # Reset.
        # ------
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset(cycles=hw_reset_cycles)
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(clock_pads, "rst_n"):
            self.comb += clock_pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]

# LiteEth PHY RGMII --------------------------------------------------------------------------------

class LiteEthPHYRGMII(LiteXModule):
    n           = 0
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, platform, clock_pads, pads, with_hw_init_reset=True, hw_reset_cycles=256):
        self.crg = LiteEthPHYRGMIICRG(platform, clock_pads, with_hw_init_reset, hw_reset_cycles, n=self.n)
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads, self.crg.cd_eth_tx.clk))
        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads, self.crg.cd_eth_rx.clk))
        self.sink, self.source = self.tx.sink, self.rx.source
        LiteEthPHYRGMII.n += 1 # FIXME: Improve.

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
