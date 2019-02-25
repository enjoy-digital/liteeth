# RGMII PHY for ECP5 Lattice FPGA
from liteeth.common import *

from migen.genlib.fsm import FSM, NextState
from migen.genlib.resetsync import AsyncResetSynchronizer

from liteeth.phy.common import *


class LiteEthPHYRGMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_oddrx1f = Signal()
        tx_data_oddrx1f = Signal(4)

        self.specials += [
            Instance("ODDRX1F",
                i_D0=sink.valid,
                i_D1=sink.valid,
                i_SCLK=ClockSignal("eth_tx"),
                i_RST=ResetSignal("eth_tx"),
                o_Q=tx_ctl_oddrx1f
            ),
            Instance("DELAYF",
                p_DEL_MODE="SCLK_ALIGNED",
                p_DEL_VALUE="DELAY0",
                i_A=tx_ctl_oddrx1f,
                i_LOADN=1,
                i_MOVE=0,
                i_DIRECTION=0,
                o_Z=pads.tx_ctl)
        ]
        for i in range(4):
            self.specials += [
                Instance("ODDRX1F",
                    i_D0=sink.data[i],
                    i_D1=sink.data[4+i],
                    i_SCLK=ClockSignal("eth_tx"),
                    i_RST=ResetSignal("eth_tx"),
                    o_Q=tx_data_oddrx1f[i]
                ),
                Instance("DELAYF",
                    p_DEL_MODE="SCLK_ALIGNED",
                    p_DEL_VALUE="DELAY0",
                    i_A=tx_data_oddrx1f[i],
                    i_LOADN=1,
                    i_MOVE=0,
                    i_DIRECTION=0,
                    o_Z=pads.tx_data[i])
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_ctl_delayf = Signal()
        rx_ctl = Signal()
        rx_ctl_reg = Signal()
        rx_data_delayf = Signal(4)
        rx_data = Signal(8)
        rx_data_reg = Signal(8)

        self.specials += [
            Instance("DELAYF",
                p_DEL_MODE="SCLK_ALIGNED",
                p_DEL_VALUE="DELAY{}".format(int(2e-9/25e-12)), # 2ns (25ps per tap)
                i_A=pads.rx_ctl,
                i_LOADN=1,
                i_MOVE=0,
                i_DIRECTION=0,
                o_Z=rx_ctl_delayf),
            Instance("IDDRX1F",
                i_D=rx_ctl_delayf,
                i_SCLK=ClockSignal("eth_rx"),
                i_RST=ResetSignal("eth_rx"),
                o_Q0=rx_ctl,
            )
        ]
        self.sync += rx_ctl_reg.eq(rx_ctl)
        for i in range(4):
            self.specials += [
                Instance("DELAYF",
                    p_DEL_MODE="SCLK_ALIGNED",
                    p_DEL_VALUE="DELAY{}".format(int(2e-9/25e-12)), # 2ns (25ps per tap)
                    i_A=pads.rx_data[i],
                    i_LOADN=1,
                    i_MOVE=0,
                    i_DIRECTION=0,
                    o_Z=rx_data_delayf[i]),
                Instance("IDDRX1F",
                    i_D=rx_data_delayf[i],
                    i_SCLK=ClockSignal("eth_rx"),
                    i_RST=ResetSignal("eth_rx"),
                    o_Q0=rx_data[i],
                    o_Q1=rx_data[i+4]
                )
            ]
        self.sync += rx_data_reg.eq(rx_data)

        rx_ctl_reg_d = Signal()
        self.sync += rx_ctl_reg_d.eq(rx_ctl_reg)

        last = Signal()
        self.comb += last.eq(~rx_ctl_reg & rx_ctl_reg_d)
        self.sync += [
            source.valid.eq(rx_ctl_reg),
            source.data.eq(Cat(rx_data_reg[:4], rx_data_reg[4:]))
        ]
        self.comb += source.last.eq(last)


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset):
        self._reset = CSRStorage()

        # # #

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)

        # RX
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX
        eth_tx_clk_o = Signal()
        self.specials += [
            Instance("ODDRX1F",
                i_D0=1,
                i_D1=0,
                i_SCLK=ClockSignal("eth_tx"),
                i_RST=ResetSignal("eth_tx"),
                o_Q=eth_tx_clk_o
            ),
            Instance("DELAYF",
                p_DEL_MODE="SCLK_ALIGNED",
                p_DEL_VALUE="DELAY{}".format(int(2e-9/25e-12)),
                i_A=eth_tx_clk_o,
                i_LOADN=1,
                i_MOVE=0,
                i_DIRECTION=0,
                o_Z=clock_pads.tx)
        ]

        # Reset
        reset = Signal()
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
    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        self.dw = 8
        self.submodules.crg = LiteEthPHYRGMIICRG(clock_pads, pads, with_hw_init_reset)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
