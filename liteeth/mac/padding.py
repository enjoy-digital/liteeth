#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# Copyright (c) 2025 Fin Maaß <f.maass@vogl-electronic.com>
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

        # drop the packet when
        # payload size < minimum ethernet payload size

        length     = Signal(max=eth_mtu)
        length_inc = Signal(4)

        # Decode Length increment from from last_be.
        self.comb += Case(sink.last_be, {
            0b00000001 : length_inc.eq(1),
            0b00000010 : length_inc.eq(2),
            0b00000100 : length_inc.eq(3),
            0b00001000 : length_inc.eq(4),
            0b00010000 : length_inc.eq(5),
            0b00100000 : length_inc.eq(6),
            0b01000000 : length_inc.eq(7),
            "default"  : length_inc.eq(dw//8)
        })

        self.sync += [
            If(sink.valid & sink.ready,
                If(sink.last,
                    length.eq(0),
                ).Else(
                    length.eq(length + length_inc)
                )
            )
        ]

        self.comb += [
            sink.connect(source, omit={"error"}),

            If(sink.valid & sink.last & ((length + length_inc) < packet_min_length),
                source.error.eq(Replicate(1, dw//8)),
            ).Else(
                source.error.eq(sink.error),
            )
        ]
