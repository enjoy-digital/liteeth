#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Ultrascale Xilinx FPGAs

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from liteeth.common import *
from liteeth.phy.common import *


class LiteEthPHYRGMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_obuf  = Signal()
        tx_data_obuf = Signal(4)

        self.specials += [
            Instance("ODDRE1",
                i_C  = ClockSignal("eth_tx"),
                i_SR = 0,
                i_D1 = sink.valid,
                i_D2 = sink.valid,
                o_Q  = tx_ctl_obuf),
            Instance("OBUF",
                i_I = tx_ctl_obuf,
                o_O = pads.tx_ctl,
            ),
        ]
        for i in range(4):
            self.specials += [
                Instance("ODDRE1",
                    i_C=ClockSignal("eth_tx"),
                    i_SR=0,
                    i_D1=sink.data[i],
                    i_D2=sink.data[4 + i],
                    o_Q=tx_data_obuf[i],
                ),
                Instance("OBUF",
                    i_I=tx_data_obuf[i],
                    o_O=pads.tx_data[i],
                ),
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module):
    def __init__(self, pads, rx_delay=2e-9):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_ctl_ibuf    = Signal()
        rx_ctl_idelay  = Signal()
        rx_ctl         = Signal()
        rx_data_ibuf   = Signal(4)
        rx_data_idelay = Signal(4)
        rx_data        = Signal(8)

        self.specials += [
            Instance("IBUF",
                i_I=pads.rx_ctl,
                o_O=rx_ctl_ibuf
            ),
            Instance("IDELAYE3",
                p_DELAY_SRC        = "IDATAIN",
                p_CASCADE          = "NONE",
                p_DELAY_TYPE       = "FIXED",
                p_DELAY_VALUE      = int(rx_delay*1e12),
                p_REFCLK_FREQUENCY = 300.0,
                p_DELAY_FORMAT     = "TIME",
                p_UPDATE_MODE      = "ASYNC",
                i_CASC_IN     = 0,
                i_CASC_RETURN = 0,
                i_CE          = 0,
                i_CLK         = 0,
                i_INC         = 0,
                i_LOAD        = 0,
                i_CNTVALUEIN  = 0,
                i_IDATAIN     = rx_ctl_ibuf,
                i_RST         = 0,
                i_EN_VTC      = 1,
                o_DATAOUT     = rx_ctl_idelay,
            ),
            Instance("IDDRE1",
                p_DDR_CLK_EDGE   = "SAME_EDGE_PIPELINED",
                p_IS_C_INVERTED  = 0,
                p_IS_CB_INVERTED = 1,
                i_C  = ClockSignal("eth_rx"),
                i_CB = ClockSignal("eth_rx"),
                i_R  = 0,
                i_D  = rx_ctl_idelay,
                o_Q1 = rx_ctl,
                o_Q2 = Signal(),
            ),
        ]
        for i in range(4):
            self.specials += [
                Instance("IBUF",
                    i_I = pads.rx_data[i],
                    o_O = rx_data_ibuf[i],
                ),
                Instance("IDELAYE3",
                    p_DELAY_SRC        = "IDATAIN",
                    p_CASCADE          = "NONE",
                    p_DELAY_TYPE       = "FIXED",
                    p_DELAY_VALUE      = int(rx_delay*1e12),
                    p_REFCLK_FREQUENCY = 300.0,
                    p_UPDATE_MODE      = "ASYNC",
                    p_DELAY_FORMAT     = "TIME",
                    i_CASC_IN     = 0,
                    i_CASC_RETURN = 0,
                    i_CE          = 0,
                    i_CLK         = 0,
                    i_INC         = 0,
                    i_LOAD        = 0,
                    i_CNTVALUEIN  = 0,
                    i_IDATAIN     = rx_data_ibuf[i],
                    i_RST         = 0,
                    i_EN_VTC      = 1,
                    o_DATAOUT     = rx_data_idelay[i],
                ),
                Instance("IDDRE1",
                    p_DDR_CLK_EDGE   = "SAME_EDGE_PIPELINED",
                    p_IS_C_INVERTED  = 0,
                    p_IS_CB_INVERTED = 1,
                    i_C  = ClockSignal("eth_rx"),
                    i_CB = ClockSignal("eth_rx"),
                    i_R  = 0,
                    i_D  = rx_data_idelay[i],
                    o_Q1 = rx_data[i],
                    o_Q2 = rx_data[i + 4],
                ),
            ]

        rx_ctl_d = Signal()
        self.sync += rx_ctl_d.eq(rx_ctl)

        last = Signal()
        self.comb += last.eq(~rx_ctl & rx_ctl_d)
        self.sync += [
            source.valid.eq(rx_ctl),
            source.data.eq(rx_data)
        ]
        self.comb += source.last.eq(last)


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9):
        self._reset = CSRStorage()

        # # #

        # RX
        self.clock_domains.cd_eth_rx = ClockDomain()
        eth_rx_clk_ibuf = Signal()
        self.specials += [
            Instance("IBUF",
                i_I = clock_pads.rx,
                o_O = eth_rx_clk_ibuf,
            ),
            Instance("BUFG",
               i_I = eth_rx_clk_ibuf,
               o_O = self.cd_eth_rx.clk,
            )
        ]

        # TX
        self.clock_domains.cd_eth_tx         = ClockDomain()
        self.clock_domains.cd_eth_tx_delayed = ClockDomain(reset_less=True)
        tx_phase = 125e6*tx_delay*360
        assert tx_phase < 360
        from litex.soc.cores.clock import USPLL
        self.submodules.pll = pll = USPLL()
        pll.register_clkin(ClockSignal("eth_rx"), 125e6)
        pll.create_clkout(self.cd_eth_tx, 125e6, with_reset=False)
        pll.create_clkout(self.cd_eth_tx_delayed, 125e6, phase=tx_phase)

        eth_tx_clk_obuf = Signal()
        self.specials += [
            Instance("ODDRE1",
                i_C  = ClockSignal("eth_tx_delayed"),
                i_SR = 0,
                i_D1 = 1,
                i_D2 = 0,
                o_Q  = eth_tx_clk_obuf
            ),
            Instance("OBUF",
                i_I = eth_tx_clk_obuf,
                o_O = clock_pads.tx,
            )
        ]

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRGMII(Module, AutoCSR):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, clock_pads, pads, with_hw_init_reset=True, tx_delay=2e-9, rx_delay=2e-9):
        self.submodules.crg = LiteEthPHYRGMIICRG(clock_pads, pads, with_hw_init_reset, tx_delay)
        self.submodules.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.submodules.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads, rx_delay))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
