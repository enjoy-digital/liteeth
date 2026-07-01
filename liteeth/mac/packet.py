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

from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *

# MAC Packet Writer Frontend -----------------------------------------------------------------------

class LiteEthMACPacketWriter(LiteXModule):
    def __init__(self, dw, depth, eth_mtu=eth_mtu_default, fifo_depth=1, timestamp=None,
        drop_when_disabled=False):
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
        assert fifo_depth >= 0
        if timestamp is not None:
            timestampbits  = len(timestamp)
            self.timestamp = Signal(timestampbits)

        # # #

        length  = Signal.like(self.length)
        error   = Signal()
        pkt_len = Signal.like(self.length)

        # Optional Packet FIFO.
        if fifo_depth:
            packet_fifo = PacketFIFO(
                eth_phy_description(dw),
                payload_depth = fifo_depth * depth,
                param_depth   = fifo_depth,
            )
            self.submodules += packet_fifo
            self.comb += sink.connect(packet_fifo.sink)
            packet_source = packet_fifo.source
        else:
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
        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(packet_source.valid,
                If(self.enable,
                    NextState("WRITE")
                ).Elif(drop_when_disabled,
                    packet_source.ready.eq(1),
                    If(packet_source.last,
                        NextState("DISCARD")
                    ).Else(
                        NextState("DISCARD-ALL")
                    )
                )
            )
        )
        fsm.act("WRITE",
            If(packet_source.valid,
                source.valid.eq(packet_source.valid),
                source.data.eq(packet_source.data),
                source.last.eq(packet_source.last),
                packet_source.ready.eq(source.ready),
                If(source.ready,
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
                    ).Elif(last_error,
                        NextValue(error, 1)
                    )
                )
            )
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
            self.sync += If(fsm.ongoing("WRITE") & packet_source.valid & source.ready & (length == 0),
                timestamp_value.eq(timestamp)
            )


# MAC Packet Reader Frontend -----------------------------------------------------------------------

class LiteEthMACPacketReader(LiteXModule):
    def __init__(self, dw, depth, fifo_depth=1, timestamp=None):
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
        assert fifo_depth >= 0
        if timestamp is not None:
            timestampbits  = len(timestamp)
            self.timestamp = Signal(timestampbits)

        # # #

        if timestamp is not None:
            timestamp_value = Signal(timestampbits)
            self.comb += self.timestamp.eq(timestamp_value)

        # Optional Packet FIFO.
        direct_read = Signal()
        if fifo_depth:
            packet_fifo = PacketFIFO(
                eth_phy_description(dw),
                payload_depth = fifo_depth * depth,
                param_depth   = fifo_depth,
            )
            self.submodules += packet_fifo
            packet_sink = packet_fifo.sink
            self.comb += packet_fifo.source.connect(source)
            self.comb += packet_sink.error.eq(0)
        else:
            packet_sink = sink
            self.comb += [
                source.valid.eq(sink.valid & direct_read),
                source.data.eq(sink.data),
                source.last.eq(sink.last),
                source.error.eq(0),
                sink.ready.eq(source.ready & direct_read),
            ]

        # Encode Length to last_be.
        length_lsb = self.length[:int(math.log2(dw/8))] if (dw != 8) else 0
        last_be    = packet_sink.last_be if fifo_depth else source.last_be
        self.comb += If(packet_sink.last,
            Case(length_lsb, {
                1         : last_be.eq(0b00000001),
                2         : last_be.eq(0b00000010),
                3         : last_be.eq(0b00000100),
                4         : last_be.eq(0b00001000),
                5         : last_be.eq(0b00010000),
                6         : last_be.eq(0b00100000),
                7         : last_be.eq(0b01000000),
                "default" : last_be.eq(2**(dw//8 - 1)),
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
        read_statements = []
        if fifo_depth:
            read_statements += [
                packet_sink.valid.eq(sink.valid),
                packet_sink.data.eq(sink.data),
                packet_sink.last.eq(sink.last),
                sink.ready.eq(packet_sink.ready),
            ]
        fsm.act("READ",
            direct_read.eq(1),
            *read_statements,
            If(sink.valid & sink.ready & sink.last,
                NextState("END")
            )
        )
        if fifo_depth:
            end_statements = [If(source.valid & source.ready & source.last,
                self.done.eq(1),
                NextState("IDLE"),
            )]
        else:
            end_statements = [
                self.done.eq(1),
                NextState("IDLE"),
            ]
        fsm.act("END",
            *end_statements,
        )
