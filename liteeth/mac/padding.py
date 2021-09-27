#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *

# MAC Padding Inserter -----------------------------------------------------------------------------

class LiteEthMACPaddingInserter(Module):
    def __init__(self, dw, padding):
        assert dw in [8, 16, 32, 64]
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        padding_limit = math.ceil(padding/(dw/8))-1
        last_be       = 2**((padding-1)%(dw//8))

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
                        source.last_be.eq(0),
                        NextState("PADDING")
                    ).Elif((counter == padding_limit) & (last_be > sink.last_be),
                        # If the right amount of data words are transmitted, but
                        # too few bytes, transmit more bytes of the word. The
                        # formerly "unused" bytes get transmitted as well
                        source.last_be.eq(last_be)
                    ). Else(
                        NextValue(counter, 0),
                    )
                )
            )
        )
        fsm.act("PADDING",
            source.valid.eq(1),
            If(counter_done,
                source.last_be.eq(last_be),
                source.last.eq(1)),
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

