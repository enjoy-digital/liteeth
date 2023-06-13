#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.common import *

# LiteEth PHY Model CRG ----------------------------------------------------------------------------

class LiteEthPHYModelCRG(LiteXModule):
    def __init__(self):
        self._reset = CSRStorage()

        # # #

        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()
        self.comb += [
            self.cd_eth_rx.clk.eq(ClockSignal("sys")),
            self.cd_eth_tx.clk.eq(ClockSignal("sys")),
        ]

        reset = self._reset.storage
        self.comb += [
            self.cd_eth_rx.rst.eq(reset),
            self.cd_eth_tx.rst.eq(reset)
        ]

# LiteEth PHY Model --------------------------------------------------------------------------------

class LiteEthPHYModel(LiteXModule):
    dw = 8
    def __init__(self, pads):
        self.crg    = LiteEthPHYModelCRG()
        self.sink   = sink   = stream.Endpoint(eth_phy_description(8))
        self.source = source = stream.Endpoint(eth_phy_description(8))

        self.comb += [
            pads.source_valid.eq(self.sink.valid),
            pads.source_data.eq(self.sink.data),
            self.sink.ready.eq(1),
        ]

        self.sync += [
            self.source.valid.eq(pads.sink_valid),
            self.source.data.eq(pads.sink_data),
        ]
        self.comb += self.source.last.eq(~pads.sink_valid & self.source.valid)
