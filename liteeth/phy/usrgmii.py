# This file is Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

# RGMII PHY for Ultrascale Xilinx FPGAs
# Tested on hardware (HTG-940) with liteeth: ad187d litex: 41fe7c

from liteeth.common import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from liteeth.phy.common import *


class LiteEthPHYRGMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_obuf = Signal()
        tx_data_obuf = Signal(4)

        self.specials += [
            Instance("ODDRE1",
                     i_C=ClockSignal("eth_tx"),
                     i_SR=0,
                     i_D1=sink.valid,
                     i_D2=sink.valid,
                     o_Q=tx_ctl_obuf),
            Instance("OBUF", i_I=tx_ctl_obuf, o_O=pads.tx_ctl)
        ]
        for i in range(4):
            self.specials += [
                Instance("ODDRE1",
                         i_C=ClockSignal("eth_tx"),
                         i_SR=0,
                         i_D1=sink.data[i],
                         i_D2=sink.data[4 + i],
                         o_Q=tx_data_obuf[i]),
                Instance("OBUF", i_I=tx_data_obuf[i], o_O=pads.tx_data[i])
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_ctl_ibuf = Signal()
        rx_ctl_idelay = Signal()
        rx_ctl = Signal()
        rx_data_ibuf = Signal(4)
        rx_data_idelay = Signal(4)
        rx_data = Signal(8)

        self.specials += [
            Instance("IBUF", i_I=pads.rx_ctl, o_O=rx_ctl_ibuf),
            # Using 0 delay throug the IDELAYE3 element.
            # DELAY_TYPE is COUNT, and _FORMAT is FIXED
            Instance("IDELAYE3",
                     # Using the following defaults in comments.
                     # p_DELAY_SRC="IDATAIN",
                     # p_CASCADE="NONE",
                     # p_DELAY_TYPE="FIXED",
                     # p_DELAY_VALUE=0,
                     # p_REFCLK_FREQUENCY=300.0, # default DELAY_FORMAT=COUNT
                     p_DELAY_FORMAT="COUNT",
                     # p_UPDATE_MODE="ASYNC", # We never update
                     i_CASC_IN=0,
                     i_CASC_RETURN=0,
                     i_CE=0,
                     i_CLK=0,
                     i_INC=0,
                     i_LOAD=0,
                     i_CNTVALUEIN=0,
                     i_IDATAIN=rx_ctl_ibuf,
                     i_RST=0,
                     i_EN_VTC=0,
                     o_DATAOUT=rx_ctl_idelay),
            Instance("IDDRE1",
                     p_DDR_CLK_EDGE="SAME_EDGE_PIPELINED",
                     p_IS_C_INVERTED=0,
                     p_IS_CB_INVERTED=1,
                     i_C=ClockSignal("eth_rx"),
                     i_CB=ClockSignal("eth_rx"),
                     i_R=0,
                     i_D=rx_ctl_idelay,
                     o_Q1=rx_ctl,  # o_Q2=,
            )
        ]
        for i in range(4):
            self.specials += [
                Instance(
                    "IBUF", i_I=pads.rx_data[i], o_O=rx_data_ibuf[i]),
                Instance("IDELAYE3",
                         # p_DELAY_SRC="IDATAIN",
                         # p_CASCADE="NONE",
                         # p_DELAY_TYPE="FIXED",
                         # p_DELAY_VALUE=0,
                         # p_UPDATE_MODE='ASYNC",
                         p_DELAY_FORMAT="COUNT",
                         i_CASC_IN=0,
                         i_CASC_RETURN=0,
                         i_CE=0,
                         i_CLK=0,
                         i_INC=0,
                         i_LOAD=0,
                         i_CNTVALUEIN=0,
                         i_IDATAIN=rx_data_ibuf[i],
                         i_RST=0,
                         i_EN_VTC=0,
                         o_DATAOUT=rx_data_idelay[i]),
                Instance("IDDRE1",
                         p_DDR_CLK_EDGE="SAME_EDGE_PIPELINED",
                         p_IS_C_INVERTED=0,
                         p_IS_CB_INVERTED=1,
                         i_C=ClockSignal("eth_rx"),
                         i_CB=ClockSignal("eth_rx"),
                         i_R=0,
                         i_D=rx_data_idelay[i],
                         o_Q1=rx_data[i],
                         o_Q2=rx_data[i + 4], )
            ]

        rx_ctl_d = Signal()
        self.sync += rx_ctl_d.eq(rx_ctl)

        last = Signal()
        self.comb += last.eq(~rx_ctl & rx_ctl_d)
        self.sync += [source.valid.eq(rx_ctl), source.data.eq(rx_data)]
        self.comb += source.last.eq(last)


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset):
        self._reset = CSRStorage()

        # # #

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.clock_domains.cd_eth_tx90 = ClockDomain(reset_less=True)

        # RX
        eth_rx_clk_ibuf = Signal()
        self.specials += [
            Instance(
                "IBUF", i_I=clock_pads.rx, o_O=eth_rx_clk_ibuf), Instance(
                    "BUFG", i_I=eth_rx_clk_ibuf, o_O=self.cd_eth_rx.clk)
        ]

        # TX
        pll_locked = Signal()
        pll_fb = Signal()
        pll_clk_tx = Signal()
        pll_clk_tx90 = Signal()
        eth_tx_clk_obuf = Signal()
        self.specials += [
            Instance(
                "PLLE2_BASE",
                p_STARTUP_WAIT="FALSE",
                o_LOCKED=pll_locked,

                # VCO @ 1000 MHz
                p_REF_JITTER1=0.01,
                p_CLKIN1_PERIOD=8.0,
                p_CLKFBOUT_MULT=8,
                p_DIVCLK_DIVIDE=1,
                i_CLKIN1=ClockSignal("eth_rx"),
                i_CLKFBIN=pll_fb,
                o_CLKFBOUT=pll_fb,

                # 125 MHz
                p_CLKOUT0_DIVIDE=8,
                p_CLKOUT0_PHASE=0.0,
                o_CLKOUT0=pll_clk_tx,

                # 125 MHz
                p_CLKOUT1_DIVIDE=8,
                p_CLKOUT1_PHASE=90.0,
                o_CLKOUT1=pll_clk_tx90),
            Instance(
                "BUFG", i_I=pll_clk_tx, o_O=self.cd_eth_tx.clk),
            Instance(
                "BUFG", i_I=pll_clk_tx90, o_O=self.cd_eth_tx90.clk),
            Instance(
                "ODDRE1",
                i_C=ClockSignal("eth_tx90"),
                i_SR=0,
                i_D1=1,
                i_D2=0,
                o_Q=eth_tx_clk_obuf),
            Instance(
                "OBUF", i_I=eth_tx_clk_obuf, o_O=clock_pads.tx)
        ]

        # Reset
        reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)

        if hasattr(pads, 'rst_n'):
            self.comb += pads.rst_n.eq(1)

        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRGMII(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        self.dw = 8
        self.submodules.crg = LiteEthPHYRGMIICRG(clock_pads, pads,
                                                 with_hw_init_reset)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(
            LiteEthPHYRGMIITX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(
            LiteEthPHYRGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
