#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY GMII TX ------------------------------------------------------------------------------

class LiteEthPHYGMIITX(LiteXModule):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        if hasattr(pads, "tx_er"):
            pads.tx_er.reset_less = True
            self.sync += pads.tx_er.eq(0)
        pads.tx_en.reset_less   = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(sink.valid),
            pads.tx_data.eq(sink.data),
            sink.ready.eq(1)
        ]

# LiteEth PHY GMII RX ------------------------------------------------------------------------------

class LiteEthPHYGMIIRX(LiteXModule):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        dv_d = Signal()
        self.sync += [
            dv_d.eq(pads.rx_dv),
            source.valid.eq(pads.rx_dv),
            source.data.eq(pads.rx_data),
        ]
        self.comb += source.last.eq(~pads.rx_dv & dv_d)

# LiteEth PHY GMII RX ------------------------------------------------------------------------------

class LiteEthPHYGMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, with_hw_init_reset, mii_mode=0, model=False):
        self._reset = CSRStorage()

        # # #

        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()

        if not model:
            # RX clock: GMII, MII Use PHY clock_pads.rx as eth_rx_clk.
            self.specials += Instance("BUFG",
                i_I = clock_pads.rx,
                o_O = ClockSignal("eth_rx"),
            )

            # TX clock: GMII: Drive clock_pads.gtx, clock_pads.tx unused.
            #           MII : Use PHY clock_pads.tx as eth_tx_clk, do not drive clock_pads.gtx.
            self.specials += DDROutput(1, mii_mode, clock_pads.gtx, ClockSignal("eth_tx"))
            eth_tx_clk = Signal()
            self.comb += [
                If(mii_mode,
                   eth_tx_clk.eq(clock_pads.tx)
                ).Else(
                   eth_tx_clk.eq(clock_pads.rx)
                )
            ]
            self.specials += Instance("BUFG",
                i_I = eth_tx_clk,
                o_O = ClockSignal("eth_tx"),
            )

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
        else:
            self.comb += [
                self.cd_eth_rx.clk.eq(ClockSignal()),
                self.cd_eth_tx.clk.eq(ClockSignal()),
            ]

# LiteEth PHY GMII ---------------------------------------------------------------------------------

class LiteEthPHYGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, clock_pads, pads, with_hw_init_reset=True, model=False):
        self.model = model
        self.crg   = LiteEthPHYGMIICRG(clock_pads, pads, with_hw_init_reset, model=model)
        self.tx    = ClockDomainsRenamer("eth_tx")(LiteEthPHYGMIITX(pads))
        self.rx    = ClockDomainsRenamer("eth_rx")(LiteEthPHYGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
