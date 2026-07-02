#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# Copyright (c) 2017 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

import math

from litex.gen import *

from liteeth.common import *

# MAC Packet Writer Frontend -----------------------------------------------------------------------

class LiteEthMACPacketWriter(LiteXModule):
    def __init__(self, dw, depth, eth_mtu=eth_mtu_default, timestamp=None, drop_when_disabled=False):
        # Endpoint / Signals.
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_packet_description(dw))

        self.enable  = Signal(reset=1)
        self.done    = Signal()
        self.drop    = Signal()
        self.error   = Signal()
        self.offset  = Signal(bits_for(depth*dw//8))
        self.length  = Signal(bits_for(depth*dw//8))

        # Parameters Check / Compute.
        assert dw in [8, 16, 32, 64]
        assert depth > 0
        if timestamp is not None:
            timestampbits  = len(timestamp)
            self.timestamp = Signal(timestampbits)

        # # #

        length  = Signal.like(self.length)
        error   = Signal()
        pkt_len = Signal.like(self.length)

        packet_source = sink
        if timestamp is not None:
            timestamp_value = Signal(timestampbits)
            self.comb += self.timestamp.eq(timestamp_value)

        # Decode Length increment from last_be.
        length_inc = Signal(4)
        self.comb += Case(packet_source.last_be, {
            0b00000001 : length_inc.eq(1),
            0b00000010 : length_inc.eq(2),
            0b00000100 : length_inc.eq(3),
            0b00001000 : length_inc.eq(4),
            0b00010000 : length_inc.eq(5),
            0b00100000 : length_inc.eq(6),
            0b01000000 : length_inc.eq(7),
            "default"  : length_inc.eq(dw//8)
        })

        next_length = Signal.like(self.length)
        last_error  = Signal()
        self.comb += [
            next_length.eq(length + length_inc),
            last_error.eq((packet_source.error & packet_source.last_be) != 0),
            self.offset.eq(length),
            self.length.eq(Mux(self.done, pkt_len, next_length)),
        ]

        def write_statements(idle=False):
            continue_statements = [NextState("WRITE")] if idle else []
            return [
                source.valid.eq(packet_source.valid),
                source.data.eq(packet_source.data),
                source.last.eq(packet_source.last),
                packet_source.ready.eq(source.ready),
                If(packet_source.valid & source.ready,
                    NextValue(length, next_length),
                    NextValue(pkt_len, next_length),
                    If(next_length > eth_mtu,
                        NextValue(error, 1),
                        If(packet_source.last,
                            NextState("DISCARD")
                        ).Else(
                            NextState("DISCARD-ALL")
                        )
                    ).Elif(packet_source.last,
                        If(error | last_error,
                            NextValue(error, 1),
                            NextState("DISCARD")
                        ).Else(
                            NextState("TERMINATE")
                        )
                    ).Else(
                        If(last_error,
                            NextValue(error, 1)
                        ),
                        *continue_statements
                    )
                )
            ]

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        if drop_when_disabled:
            fsm.act("IDLE",
                If(self.enable,
                    *write_statements(idle=True)
                ).Else(
                    packet_source.ready.eq(1),
                    If(packet_source.valid,
                        If(packet_source.last,
                            NextState("DISCARD")
                        ).Else(
                            NextState("DISCARD-ALL")
                        )
                    )
                )
            )
        else:
            fsm.act("IDLE",
                If(self.enable,
                    *write_statements(idle=True)
                )
            )
        fsm.act("WRITE",
            *write_statements()
        )
        fsm.act("DISCARD-ALL",
            packet_source.ready.eq(1),
            If(packet_source.valid & packet_source.last,
                NextState("DISCARD")
            )
        )
        fsm.act("DISCARD",
            self.drop.eq(1),
            self.error.eq(error),
            NextValue(length, 0),
            NextValue(pkt_len, 0),
            NextValue(error, 0),
            NextState("IDLE")
        )
        fsm.act("TERMINATE",
            self.done.eq(1),
            NextValue(length, 0),
            NextValue(pkt_len, 0),
            NextValue(error, 0),
            NextState("IDLE")
        )
        if timestamp is not None:
            self.sync += If(
                (fsm.ongoing("IDLE") | fsm.ongoing("WRITE")) &
                self.enable & packet_source.valid & packet_source.ready & (length == 0),
                timestamp_value.eq(timestamp)
            )


# MAC Packet Reader Frontend -----------------------------------------------------------------------

class LiteEthMACPacketReader(LiteXModule):
    def __init__(self, dw, depth, timestamp=None):
        # Endpoint / Signals.
        self.sink   = sink   = stream.Endpoint(eth_packet_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        self.enable      = Signal()
        self.idle        = Signal()
        self.done        = Signal()

        self.length      = Signal(bits_for(depth*dw//8))

        # Parameters Check / Compute.
        assert dw in [8, 16, 32, 64]
        assert depth > 0
        if timestamp is not None:
            timestampbits  = len(timestamp)
            self.timestamp = Signal(timestampbits)

        # # #

        if timestamp is not None:
            timestamp_value = Signal(timestampbits)
            self.comb += self.timestamp.eq(timestamp_value)

        direct_read = Signal()
        self.comb += [
            source.valid.eq(sink.valid & direct_read),
            source.data.eq(sink.data),
            source.last.eq(sink.last),
            source.error.eq(0),
            sink.ready.eq(source.ready & direct_read),
        ]

        # Encode Length to last_be.
        length_lsb = self.length[:int(math.log2(dw/8))] if (dw != 8) else 0
        self.comb += If(source.last,
            Case(length_lsb, {
                1         : source.last_be.eq(0b00000001),
                2         : source.last_be.eq(0b00000010),
                3         : source.last_be.eq(0b00000100),
                4         : source.last_be.eq(0b00001000),
                5         : source.last_be.eq(0b00010000),
                6         : source.last_be.eq(0b00100000),
                7         : source.last_be.eq(0b01000000),
                "default" : source.last_be.eq(2**(dw//8 - 1)),
            })
        )

        if timestamp is not None:
            self.sync += If(self.idle & self.enable, timestamp_value.eq(timestamp))

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self.idle.eq(1),
            If(self.enable,
                NextState("READ")
            )
        )
        fsm.act("READ",
            direct_read.eq(1),
            If(sink.valid & sink.ready & sink.last,
                NextState("END")
            )
        )
        fsm.act("END",
            self.done.eq(1),
            NextState("IDLE"),
        )
