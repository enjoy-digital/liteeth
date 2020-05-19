# This file is Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

# RGMII PHY for ECP5 Lattice FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.io import DDROutput, DDRInput

from liteeth.common import *
from liteeth.phy.common import *
from liteeth.phy.rgmii import *


class LiteEthPHYRGMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_oddrx1f  = Signal()
        tx_data_oddrx1f = Signal(4)

        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = sink.valid,
                i2  = sink.valid,
                o   = tx_ctl_oddrx1f),
            Instance("DELAYF",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = "DELAY0",
                i_LOADN     = 1,
                i_MOVE      = 0,
                i_DIRECTION = 0,
                i_A         = tx_ctl_oddrx1f,
                o_Z         = pads.tx_ctl)
        ]
        for i in range(4):
            self.specials += [
                DDROutput(
                    clk = ClockSignal("eth_tx"),
                    i1  = sink.data[i],
                    i2  = sink.data[4+i],
                    o   = tx_data_oddrx1f[i]),
                Instance("DELAYF",
                    p_DEL_MODE  = "SCLK_ALIGNED",
                    p_DEL_VALUE = "DELAY0",
                    i_LOADN     = 1,
                    i_MOVE      = 0,
                    i_DIRECTION = 0,
                    i_A         = tx_data_oddrx1f[i],
                    o_Z         = pads.tx_data[i]
                )
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module):
    def __init__(self, pads, rx_delay=2e-9):
        self.source = source = stream.Endpoint(eth_phy_description(8))
        self.status = Record(phy_status_layout)

        # # #

        rx_delay_taps = int(rx_delay/25e-12) # 25ps per tap
        assert rx_delay_taps < 128

        rx_ctl_delayf  = Signal()
        rx_ctl_r       = Signal()
        rx_ctl_f       = Signal()
        rx_ctl_r_reg   = self.status.ctl_r
        rx_ctl_f_reg   = self.status.ctl_f
        rx_data_delayf = Signal(4)
        rx_data        = Signal(8)
        rx_data_reg    = self.status.data

        self.specials += [
            Instance("DELAYF",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = "DELAY{}".format(rx_delay_taps),
                i_LOADN     = 1,
                i_MOVE      = 0,
                i_DIRECTION = 0,
                i_A         = pads.rx_ctl,
                o_Z         = rx_ctl_delayf),
            DDRInput(
                clk = ClockSignal("eth_rx"),
                i   = rx_ctl_delayf,
                o1  = rx_ctl_r,
                o2  = rx_ctl_f
            )
        ]
        self.sync += [
            rx_ctl_r_reg.eq(rx_ctl_r),
            rx_ctl_f_reg.eq(rx_ctl_f)
        ]

        for i in range(4):
            self.specials += [
                Instance("DELAYF",
                    p_DEL_MODE  = "SCLK_ALIGNED",
                    p_DEL_VALUE = "DELAY{}".format(rx_delay_taps),
                    i_LOADN     = 1,
                    i_MOVE      = 0,
                    i_DIRECTION = 0,
                    i_A         = pads.rx_data[i],
                    o_Z         = rx_data_delayf[i]),
                DDRInput(
                    clk = ClockSignal("eth_rx"),
                    i   = rx_data_delayf[i],
                    o1  = rx_data[i],
                    o2  = rx_data[i+4]
                )
            ]
        self.sync += rx_data_reg.eq(rx_data)

        rx_ctl_r_reg_d = Signal()
        self.sync += rx_ctl_r_reg_d.eq(rx_ctl_r_reg)

        last = Signal()
        self.comb += last.eq(~rx_ctl_r_reg & rx_ctl_r_reg_d)
        self.sync += [
            source.valid.eq(rx_ctl_r_reg),
            source.data.eq(rx_data_reg)
        ]
        self.comb += source.last.eq(last)


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9):
        self._reset = CSRStorage()

        # # #

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)

        # RX
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX
        tx_delay_taps = int(tx_delay/25e-12) # 25ps per tap
        assert tx_delay_taps < 128

        eth_tx_clk_o = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = 1,
                i2  = 0,
                o   = eth_tx_clk_o),
            Instance("DELAYF",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = "DELAY{}".format(tx_delay_taps),
                i_LOADN     = 1,
                i_MOVE      = 0,
                i_DIRECTION = 0,
                i_A         = eth_tx_clk_o,
                o_Z         = clock_pads.tx)
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
    def __init__(self, clock_pads, pads, with_hw_init_reset=True, tx_delay=2e-9, rx_delay=2e-9, inband_status=True):
        self.submodules.crg = LiteEthPHYRGMIICRG(clock_pads, pads, with_hw_init_reset, tx_delay)
        self.submodules.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.submodules.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads, rx_delay))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)

        if inband_status:
            self.submodules.status = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIStatus())
            self.comb += self.status.phy.eq(self.rx.status)
