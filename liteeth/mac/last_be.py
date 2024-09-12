#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from liteeth.common     import *
from liteeth.mac.common import LiteEthLastHandler

# MAC TX Last BE -----------------------------------------------------------------------------------

class LiteEthMACTXLastBE(LiteXModule):
    def __init__(self, dw):
        self.sink   =   sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        self.last_handler = LiteEthLastHandler(layout=eth_phy_description(dw))
        self.comb += [
            sink.connect(self.last_handler.sink),
            self.last_handler.source.connect(source),
        ]

# MAC RX Last BE -----------------------------------------------------------------------------------

class LiteEthMACRXLastBE(Module):
    def __init__(self, dw):
        self.sink   =   sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        self.comb += [
            sink.connect(source),
            If(dw == 8,
                # 8bit PHYs will only drive last, thus `last_be` must be
                # controlled accordingly. PHYs > 8bit must drive `last_be`
                # themselves.
                source.last_be.eq(sink.last)
            )
        ]
