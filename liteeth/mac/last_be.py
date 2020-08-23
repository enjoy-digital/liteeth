#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

# MAC TX Last BE -----------------------------------------------------------------------------------

class LiteEthMACTXLastBE(Module):
    def __init__(self, dw):
        self.sink   = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        ongoing = Signal(reset=1)
        self.sync += [
            If(sink.valid & sink.ready,
                If(sink.last,
                    ongoing.eq(1)
                ).Elif(sink.last_be,
                    ongoing.eq(0)
                )
            )
        ]
        self.comb += [
            source.valid.eq(sink.valid & ongoing),
            source.last.eq(sink.last_be),
            source.data.eq(sink.data),
            sink.ready.eq(source.ready)
        ]

# MAC RX Last BE -----------------------------------------------------------------------------------

class LiteEthMACRXLastBE(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        self.comb += [
            sink.connect(source),
            source.last_be.eq(sink.last)
        ]
