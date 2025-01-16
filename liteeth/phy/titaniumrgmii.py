#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Franck Jullien <franck.jullien@collshade.fr>
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Titanium Efinix FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import ClkInput, ClkOutput, DDROutput, DDRInput
from litex.build.generic_platform import *
from litex.soc.cores.clock import *

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
    def __init__(self, pads, clk):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        # TX Data IOs.
        # ------------
        tx_data_h = Signal(4)
        tx_data_l = Signal(4)
        self.specials += DDROutput(
            i1  = tx_data_h,
            i2  = tx_data_l,
            o   = pads.tx_data,
            clk = clk,
        )

        # TX Ctl IOs.
        # -----------
        tx_ctl_h = Signal()
        tx_ctl_l = Signal()
        self.specials += DDROutput(
            i1  = tx_ctl_h,
            i2  = tx_ctl_l,
            o   = pads.tx_ctl,
            clk = clk,
        )

        # Logic.
        # ------
        self.comb += sink.ready.eq(1)
        self.sync += [
            tx_ctl_h.eq(sink.valid),
            tx_ctl_l.eq(sink.valid),
            tx_data_h.eq(sink.data[:4]),
            tx_data_l.eq(sink.data[4:]),
        ]

# LiteEth PHY RGMII RX -----------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, pads, clk):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        # RX Data IOs.
        # ------------
        rx_data_h = Signal(4)
        rx_data_l = Signal(4)
        self.specials += DDRInput(
            i   = pads.rx_data,
            o1  = rx_data_h,
            o2  = rx_data_l,
            clk = clk,
        )

        # RX Ctl IOs.
        # -----------
        rx_ctl_h = Signal()
        rx_ctl_l = Signal()
        self.specials += DDRInput(
            i   = pads.rx_ctl,
            o1  = rx_ctl_h,
            o2  = rx_ctl_l,
            clk = clk,
        )

        rx_ctl   = rx_ctl_h
        rx_ctl_d = Signal()
        self.sync += rx_ctl_d.eq(rx_ctl)

        # Logic.
        # ------
        last    = Signal()
        rx_data_lsb = Signal(4)
        rx_data_msb = Signal(4)
        self.comb += rx_data_msb.eq(rx_data_l)
        self.sync += rx_data_lsb.eq(rx_data_h)
        self.sync += [
            last.eq(~rx_ctl & rx_ctl_d),
            source.valid.eq(rx_ctl_d),
            source.data.eq(Cat(rx_data_lsb, rx_data_msb)),
        ]
        self.comb += source.last.eq(last)

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, platform, clock_pads, with_hw_init_reset, hw_reset_cycles=256, n=0):
        self._reset = CSRStorage()

        # # #

        # Clk Domains.
        # ------------
        self.cd_eth_rx         = ClockDomain(name=f"eth{n}_rx")
        self.cd_eth_tx         = ClockDomain(name=f"eth{n}_tx")
        self.cd_eth_tx_delayed = ClockDomain(name=f"eth{n}_tx_delayed", reset_less=True)

        # RX Clk.
        # -------
        self.specials += ClkInput(
            i = clock_pads.rx,
            o = self.cd_eth_rx.clk,
        )

        # TX PLL.
        # -------
        self.pll = pll = TITANIUMPLL(platform)
        pll.register_clkin(self.cd_eth_rx.clk,    freq=125e6)
        pll.create_clkout(self.cd_eth_rx,         freq=125e6, phase=0,  with_reset=False)
        pll.create_clkout(self.cd_eth_tx,         freq=125e6, phase=0,  with_reset=False)
        pll.create_clkout(self.cd_eth_tx_delayed, freq=125e6, phase=90)

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
        self.tx  = ClockDomainsRenamer(f"eth{self.n}_tx")(LiteEthPHYRGMIITX(pads, self.crg.cd_eth_tx.clk))
        self.rx  = ClockDomainsRenamer(f"eth{self.n}_rx")(LiteEthPHYRGMIIRX(pads, self.crg.cd_eth_rx.clk))
        self.sink, self.source = self.tx.sink, self.rx.source
        LiteEthPHYRGMII.n += 1 # FIXME: Improve.

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
