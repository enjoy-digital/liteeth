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

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            sink.connect(source, omit={"last", "last_be"}),
            source.last.eq(sink.last_be),
            If(sink.valid & sink.ready,
                # If last Byte but not last packet token.
                If(sink.last_be & ~sink.last,
                    NextState("WAIT-LAST")
                )
            )
        )
        fsm.act("WAIT-LAST",
            # Accept incoming stream until we receive last packet token.
            sink.ready.eq(1),
            If(sink.valid & sink.last,
                NextState("COPY")
            )
        )

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
