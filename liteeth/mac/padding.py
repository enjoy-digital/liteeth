# This file is Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# This file is Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# License: BSD

import math

from liteeth.common import *

# MAC Padding Inserter -----------------------------------------------------------------------------

class LiteEthMACPaddingInserter(Module):
    def __init__(self, dw, padding):
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        padding_limit = math.ceil(padding/(dw/8))-1

        counter      = Signal(16)
        counter_done = Signal()
        self.comb += counter_done.eq(counter >= padding_limit)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.connect(source),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(sink.last,
                    If(~counter_done,
                        source.last.eq(0),
                        NextState("PADDING")
                    ).Else(
                        NextValue(counter, 0),
                    )
                )
            )
        )
        fsm.act("PADDING",
            source.valid.eq(1),
            source.last.eq(counter_done),
            source.data.eq(0),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(counter_done,
                    NextValue(counter, 0),
                    NextState("IDLE")
                )
            )
        )


# MAC Padding Checker ------------------------------------------------------------------------------

class LiteEthMACPaddingChecker(Module):
    def __init__(self, dw, packet_min_length):
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        # TODO: see if we should drop the packet when
        # payload size < minimum ethernet payload size
        self.comb += sink.connect(source)

