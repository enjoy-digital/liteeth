#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Spartan6 Xilinx FPGA

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
            Instance("ODDR2",
                p_DDR_ALIGNMENT = "C0",
                p_SRTYPE        = "ASYNC",
                o_Q  = tx_ctl_obuf,
                i_C0 = ClockSignal("eth_tx"),
                i_C1 = ~ClockSignal("eth_tx"),
                i_CE = 1,
                i_D0 = sink.valid,
                i_D1 = sink.valid,
                i_R  = ResetSignal("eth_tx"),
                i_S  = 0
            ),
            Instance("IODELAY2",
                p_IDELAY_TYPE  = "FIXED",
                p_ODELAY_VALUE = 0,
                p_DELAY_SRC    = "ODATAIN",
                o_DOUT    = pads.tx_ctl,
                i_CAL     = 0,
                i_CE      = 0,
                i_CLK     = 0,
                i_IDATAIN = 0,
                i_INC     = 0,
                i_IOCLK0  = 0,
                i_IOCLK1  = 0 ,
                i_ODATAIN = tx_ctl_obuf,
                i_RST     = 0,
                i_T       = 0,
            )
        ]
        for i in range(4):
            self.specials += [
                Instance("ODDR2",
                    p_DDR_ALIGNMENT = "C0",
                    p_SRTYPE        = "ASYNC",
                    o_Q  = tx_data_obuf[i],
                    i_C0 = ClockSignal("eth_tx"),
                    i_C1 = ~ClockSignal("eth_tx"),
                    i_CE = 1,
                    i_D0 = sink.data[i],
                    i_D1 = sink.data[4+i],
                    i_R  = ResetSignal("eth_tx"),
                    i_S  = 0,
                ),
                Instance("IODELAY2",
                    p_IDELAY_TYPE  = "FIXED",
                    p_ODELAY_VALUE = 0,
                    p_DELAY_SRC    = "ODATAIN",
                    o_DOUT    = pads.tx_data[i],
                    i_CAL     = 0,
                    i_CE      = 0,
                    i_CLK     = 0,
                    i_IDATAIN = 0,
                    i_INC     = 0,
                    i_IOCLK0  = 0,
                    i_IOCLK1  = 0,
                    i_ODATAIN = tx_data_obuf[i],
                    i_RST     = 0,
                    i_T       = 0,
                )
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module):
    def __init__(self, pads, rx_delay=2e-9):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_delay_taps = int(rx_delay/50e-12) # 50ps per tap
        assert rx_delay_taps < 256

        rx_ctl_ibuf    = Signal()
        rx_ctl_idelay  = Signal()
        rx_ctl         = Signal()
        rx_ctl_reg     = Signal()
        rx_data_ibuf   = Signal(4)
        rx_data_idelay = Signal(4)
        rx_data        = Signal(8)
        rx_data_reg    = Signal(8)

        self.specials += [
            Instance("IBUF",
                i_I = pads.rx_ctl,
                o_O = rx_ctl_ibuf,
            ),
            Instance("IODELAY2",
                p_IDELAY_TYPE  = "FIXED",
                p_ODELAY_VALUE = rx_delay_taps,
                p_DELAY_SRC    = "IDATAIN",
                o_DATAOUT = rx_ctl_idelay,
                i_CAL     = 0,
                i_CE      = 0,
                i_CLK     = 0,
                i_IDATAIN = rx_ctl_ibuf,
                i_INC     = 0,
                i_IOCLK0  = 0,
                i_IOCLK1  = 0,
                i_ODATAIN = 0,
                i_RST     = 0,
                i_T       = 1,
            ),
            Instance("IDDR2",
                p_DDR_ALIGNMENT = "C0",
                o_Q0 = rx_ctl,
                i_C0 = ClockSignal("eth_rx"),
                i_C1 = ~ClockSignal("eth_rx"),
                i_CE = 1,
                i_D  = rx_ctl_idelay,
                i_R  = 0,
                i_S  = 0,
            )
        ]
        self.sync += rx_ctl_reg.eq(rx_ctl)
        for i in range(4):
            self.specials += [
                Instance("IBUF",
                    i_I = pads.rx_data[i],
                    o_O = rx_data_ibuf[i],
                ),
                Instance("IODELAY2",
                    p_IDELAY_TYPE  = "FIXED",
                    p_ODELAY_VALUE = rx_delay_taps,
                    p_DELAY_SRC    = "IDATAIN",
                    o_DATAOUT = rx_data_idelay[i],
                    i_CAL     = 0,
                    i_CE      = 0,
                    i_CLK     = 0,
                    i_IDATAIN = rx_data_ibuf[i],
                    i_INC     = 0,
                    i_IOCLK0  = 0,
                    i_IOCLK1  = 0,
                    i_ODATAIN = 0,
                    i_RST     = 0,
                    i_T       = 1,
                ),
                Instance("IDDR2",
                    p_DDR_ALIGNMENT = "C0",
                    o_Q0 = rx_data[i],
                    o_Q1 = rx_data[i+4],
                    i_C0 = ClockSignal("eth_rx"),
                    i_C1 = ~ClockSignal("eth_rx"),
                    i_CE = 1,
                    i_D  = rx_data_idelay[i],
                    i_R  = 0,
                    i_S  = 0,
                )
            ]
        self.sync += rx_data_reg.eq(rx_data)

        rx_ctl_reg_d = Signal()
        self.sync += rx_ctl_reg_d.eq(rx_ctl_reg)

        last = Signal()
        self.comb += last.eq(~rx_ctl_reg & rx_ctl_reg_d)
        self.sync += [
            source.valid.eq(rx_ctl_reg),
            source.data.eq(Cat(rx_data_reg[:4], rx_data[4:]))
        ]
        self.comb += source.last.eq(last)


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9):
        self._reset = CSRStorage()

        # # #

        # RX clock
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
            ),
        ]

        # TX clock
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)
        tx_delay_taps = int(tx_delay/50e-12) # 50ps per tap
        assert tx_delay_taps < 256

        eth_tx_clk_o = Signal()
        self.specials += [
            Instance("ODDR2",
                p_DDR_ALIGNMENT = "C0",
                p_SRTYPE        = "ASYNC",
                o_Q  = eth_tx_clk_o,
                i_C0 = ClockSignal("eth_tx"),
                i_C1 = ~ClockSignal("eth_tx"),
                i_CE = 1,
                i_D0 = 1,
                i_D1 = 0,
                i_R  = ResetSignal("eth_tx"),
                i_S  = 0,
            ),
            Instance("IODELAY2",
                p_IDELAY_TYPE  = "FIXED",
                p_ODELAY_VALUE = tx_delay_taps,
                p_DELAY_SRC    = "ODATAIN",
                o_DOUT    = clock_pads.tx,
                i_CAL     = 0,
                i_CE      = 0,
                i_CLK     = 0,
                i_IDATAIN = 0,
                i_INC     = 0,
                i_IOCLK0  = 0,
                i_IOCLK1  = 0,
                i_ODATAIN = eth_tx_clk_o,
                i_RST     = 0,
                i_T       = 0,
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
