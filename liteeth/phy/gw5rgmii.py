#
# This file is part of LiteEth.
#
# Copyright (c) 2023 Icenowy Zheng <uwu@icenowy.me>
# Based on ecp5rgmii.py, which is:
#   Copyright (c) 2019-2023 Florent Kermarrec <florent@enjoy-digital.fr>
#   Copyright (c) 2020 Shawn Hoffman <godisgovernment@gmail.com>
#
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Gowin GW5A FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput, DDRInput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
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
                o   = tx_ctl_oddrx1f,
            ),
            Instance("IODELAY",
                p_DYN_DLY_EN   = "FALSE",
                p_ADAPT_EN     = "FALSE",
                p_C_STATIC_DLY = 0,
                i_SDTAP        = 0,
                i_DLYSTEP      = Constant(0, 8),
                i_VALUE        = 0,
                i_DI           = tx_ctl_oddrx1f,
                o_DF           = Open(),
                o_DO           = pads.tx_ctl,
            )
        ]
        for i in range(4):
            self.specials += [
                DDROutput(
                    clk = ClockSignal("eth_tx"),
                    i1  = sink.data[i],
                    i2  = sink.data[4+i],
                    o   = tx_data_oddrx1f[i],
                ),
                Instance("IODELAY",
                    p_DYN_DLY_EN   = "FALSE",
                    p_ADAPT_EN     = "FALSE",
                    p_C_STATIC_DLY = 0,
                    i_SDTAP        = 0,
                    i_DLYSTEP      = Constant(0, 8),
                    i_VALUE        = 0,
                    i_DI           = tx_data_oddrx1f[i],
                    o_DF           = Open(),
                    o_DO           = pads.tx_data[i],
                )
            ]
        self.comb += sink.ready.eq(1)

# LiteEth PHY RGMII RX -----------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, pads, rx_delay=2e-9):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        rx_delay_taps = int(rx_delay/12.5e-12) # 12.5ps per tap
        assert rx_delay_taps < 256

        rx_ctl_delayf  = Signal()
        rx_ctl         = Signal()
        rx_data_delayf = Signal(4)
        rx_data        = Signal(8)

        self.specials += [
            Instance("IODELAY",
                p_DYN_DLY_EN   = "FALSE",
                p_ADAPT_EN     = "FALSE",
                p_C_STATIC_DLY = rx_delay_taps,
                i_SDTAP        = 0,
                i_DLYSTEP      = Constant(0, 8),
                i_VALUE        = 0,
                i_DI           = pads.rx_ctl,
                o_DF           = Open(),
                o_DO           = rx_ctl_delayf,
            ),
            DDRInput(
                clk = ClockSignal("eth_rx"),
                i   = rx_ctl_delayf,
                o1  = rx_ctl,
                o2  = Open()
            )
        ]
        for i in range(4):
            self.specials += [
                Instance("IODELAY",
                    p_DYN_DLY_EN   = "FALSE",
                    p_ADAPT_EN     = "FALSE",
                    p_C_STATIC_DLY = rx_delay_taps,
                    i_SDTAP        = 0,
                    i_DLYSTEP      = Constant(0, 8),
                    i_VALUE        = 0,
                    i_DI           = pads.rx_data[i],
                    o_DF           = Open(),
                    o_DO           = rx_data_delayf[i]),
                DDRInput(
                    clk = ClockSignal("eth_rx"),
                    i   = rx_data_delayf[i],
                    o1  = rx_data[i],
                    o2  = rx_data[i+4],
                )
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

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9, tx_clk=None):
        self._reset = CSRStorage()

        # # #

        # RX Clock
        self.cd_eth_rx = ClockDomain()
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX Clock
        self.cd_eth_tx = ClockDomain()
        if isinstance(tx_clk, Signal):
            self.comb += self.cd_eth_tx.clk.eq(tx_clk)
        else:
            self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)

        tx_delay_taps = int(tx_delay/12.5e-12) # 12.5ps per tap
        assert tx_delay_taps < 256

        self._txdelay_taps = CSRStorage(8, reset=tx_delay_taps)
        eth_tx_clk_o = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = 1,
                i2  = 0,
                o   = eth_tx_clk_o,
            ),
            Instance("IODELAY",
                p_DYN_DLY_EN   = "TRUE",
                p_ADAPT_EN     = "FALSE",
                p_C_STATIC_DLY = tx_delay_taps,
                i_DI           = eth_tx_clk_o,
                i_DLYSTEP      = self._txdelay_taps.storage,
                i_SDTAP        = 1,
                i_VALUE        = 0, # FIXME
                o_DF           = Open(),
                o_DO           = clock_pads.tx,
            )
        ]

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, clock_pads, pads, with_hw_init_reset=True,
        tx_delay           = 2e-9,
        rx_delay           = 2e-9,
        tx_clk             = None,
        ):
        self.crg = LiteEthPHYRGMIICRG(clock_pads, pads, with_hw_init_reset, tx_delay, tx_clk)
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(pads))
        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(pads, rx_delay))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
