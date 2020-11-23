#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Shawn Hoffman <godisgovernment@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for ECP5 Lattice FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.io import DDROutput, DDRInput

from liteeth.common import *
from liteeth.phy.common import *


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
            Instance("DELAYG",
                p_DEL_VALUE = 0,
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
                Instance("DELAYG",
                    p_DEL_VALUE = 0,
                    i_A         = tx_data_oddrx1f[i],
                    o_Z         = pads.tx_data[i]
                )
            ]
        self.comb += sink.ready.eq(1)


class LiteEthPHYRGMIIRX(Module, AutoCSR):
    def __init__(self, pads, rx_delay=2e-9, with_inband_status=True):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        if with_inband_status:
            self.inband_status = CSRStatus(fields=[
                CSRField("link_status", size=1, values=[
                    ("``0b0``", "Link down."),
                    ("``0b1``", "Link up."),
                ]),
                CSRField("clock_speed", size=1, values=[
                    ("``0b00``", "2.5MHz   (10Mbps)."),
                    ("``0b01``", "25MHz   (100MBps)."),
                    ("``0b10``", "125MHz (1000MBps)."),
                ]),
                CSRField("duplex_status", size=1, values=[
                    ("``0b0``", "Half-duplex."),
                    ("``0b1``", "Full-duplex."),
                ]),
            ])

        # # #

        rx_delay_taps = int(rx_delay/25e-12) # 25ps per tap
        assert rx_delay_taps < 128

        rx_ctl_delayf  = Signal()
        rx_ctl         = Signal(2)
        rx_ctl_reg     = Signal(2)
        rx_data_delayf = Signal(4)
        rx_data        = Signal(8)
        rx_data_reg    = Signal(8)

        self.specials += [
            Instance("DELAYG",
                p_DEL_VALUE = rx_delay_taps,
                i_A         = pads.rx_ctl,
                o_Z         = rx_ctl_delayf),
            DDRInput(
                clk = ClockSignal("eth_rx"),
                i   = rx_ctl_delayf,
                o1  = rx_ctl[0],
                o2  = rx_ctl[1]
            )
        ]
        self.sync += rx_ctl_reg.eq(rx_ctl)
        for i in range(4):
            self.specials += [
                Instance("DELAYG",
                    p_DEL_VALUE = rx_delay_taps,
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

        rx_ctl_reg_d = Signal(2)
        self.sync += rx_ctl_reg_d.eq(rx_ctl_reg)

        last = Signal()
        self.comb += last.eq(~rx_ctl_reg[0] & rx_ctl_reg_d[0])
        self.sync += [
            source.valid.eq(rx_ctl_reg[0]),
            source.data.eq(rx_data_reg)
        ]
        self.comb += source.last.eq(last)

        if with_inband_status:
            self.sync += [
                If(rx_ctl == 0b00,
                    self.inband_status.fields.link_status.eq(rx_data[0]),
                    self.inband_status.fields.clock_speed.eq(rx_data[1:3]),
                    self.inband_status.fields.duplex_status.eq(rx_data[3]),
                )
            ]


class LiteEthPHYRGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9):
        self._reset = CSRStorage()

        # # #

        # RX Clock
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX Clock
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)
        tx_delay_taps = int(tx_delay/25e-12) # 25ps per tap
        assert tx_delay_taps < 128

        eth_tx_clk_o = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = 1,
                i2  = 0,
                o   = eth_tx_clk_o),
            Instance("DELAYG",
                p_DEL_VALUE = tx_delay_taps,
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
    def __init__(self, clock_pads, pads, with_hw_init_reset=True,
        tx_delay           = 2e-9,
        rx_delay           = 2e-9,
        with_inband_status = True):
        self.submodules.crg = LiteEthPHYRGMIICRG(clock_pads, pads, with_hw_init_reset, tx_delay)
        self.submodules.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.submodules.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads, rx_delay, with_inband_status))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
