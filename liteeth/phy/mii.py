#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY MII TX -------------------------------------------------------------------------------

class LiteEthPHYMIITX(LiteXModule):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        if hasattr(pads, "tx_er"):
            pads.tx_er.reset_less = True
            self.sync += pads.tx_er.eq(0)
        self.converter = converter = stream.Converter(8, 4)
        self.comb += [
            converter.sink.valid.eq(sink.valid),
            converter.sink.data.eq(sink.data),
            sink.ready.eq(converter.sink.ready),
            converter.source.ready.eq(1),
        ]
        pads.tx_en.reset_less   = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(converter.source.valid),
            pads.tx_data.eq(converter.source.data),
        ]

# LiteEth PHY MII RX -------------------------------------------------------------------------------

class LiteEthPHYMIIRX(LiteXModule):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        converter = stream.Converter(4, 8)
        converter = ResetInserter()(converter)
        self.submodules += converter

        self.sync += [
            converter.reset.eq(~pads.rx_dv),
            converter.sink.valid.eq(1),
            converter.sink.data.eq(pads.rx_data),
        ]
        self.comb += [
            converter.sink.last.eq(~pads.rx_dv),
            converter.source.connect(source),
        ]

# LiteEth PHY MII RX -------------------------------------------------------------------------------

class LiteEthPHYMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, with_hw_init_reset):
        self._reset = CSRStorage()

        # # #

        if hasattr(clock_pads, "phy"):
            self.sync.base50 += clock_pads.phy.eq(~clock_pads.phy)

        # RX/TX clocks
        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)
        self.comb += self.cd_eth_tx.clk.eq(clock_pads.tx)

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

# LiteEth PHY MII ----------------------------------------------------------------------------------

class LiteEthPHYMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 25e6
    rx_clk_freq = 25e6
    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        self.crg = LiteEthPHYMIICRG(clock_pads, pads, with_hw_init_reset)
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYMIITX(pads))
        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
