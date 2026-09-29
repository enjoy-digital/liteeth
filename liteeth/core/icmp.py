#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *
from liteeth.fifo import PacketDropFIFO
from litex.soc.interconnect.packet import Depacketizer, Packetizer

# ICMP TX ------------------------------------------------------------------------------------------

class LiteEthICMPPacketizer(Packetizer):
    def __init__(self, dw=8):
        Packetizer.__init__(self,
            eth_icmp_description(dw),
            eth_ipv4_user_description(dw),
            icmp_header
        )


class LiteEthICMPTX(LiteXModule):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_icmp_user_description(dw))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(dw))

        # # #

        # Packetizer.
        self.packetizer = packetizer = LiteEthICMPPacketizer(dw)
        self.comb += sink.connect(packetizer.sink, keep={
            "valid",
            "last",
            "ready",
            "msgtype",
            "code",
            "checksum",
            "quench",
            "data",
            "be"
        })

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(packetizer.source.valid,
                NextState("SEND")
            )
        )
        self.comb += [
            packetizer.source.connect(source, omit={"valid", "ready"}),
            source.length.eq(sink.length + icmp_header.length),
            source.protocol.eq(icmp_protocol),
            source.ip_address.eq(sink.ip_address),
        ]
        fsm.act("SEND",
            packetizer.source.connect(source, keep={"valid", "ready"}),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )

# ICMP RX ------------------------------------------------------------------------------------------

class LiteEthICMPDepacketizer(Depacketizer):
    def __init__(self, dw=8):
        Depacketizer.__init__(self,
            eth_ipv4_user_description(dw),
            eth_icmp_description(dw),
            icmp_header)


class LiteEthICMPRX(LiteXModule):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_ipv4_user_description(dw))
        self.source = source = stream.Endpoint(eth_icmp_user_description(dw))

        # # #

        # Depacketizer.
        self.depacketizer = depacketizer = LiteEthICMPDepacketizer(dw)
        self.comb += sink.connect(depacketizer.sink)

        # FSM.
        count = Signal(17)
        self.fsm = fsm = FSM(reset_state="IDLE")
        # Keep the ready/valid wiring outside the FSM's combinational block. This also avoids
        # simulator delta-cycle feedback between adjacent depacketizer and receiver FSMs.
        self.comb += [
            source.valid.eq(depacketizer.source.valid & fsm.ongoing("RECEIVE")),
            depacketizer.source.ready.eq(fsm.ongoing("DROP") |
                (fsm.ongoing("RECEIVE") & source.ready)),
        ]
        fsm.act("IDLE",
            NextValue(count, dw//8),
            If(depacketizer.source.valid,
                NextState("DROP"),
                If((sink.protocol == icmp_protocol) & (sink.length > icmp_header.length),
                    If((depacketizer.source.msgtype == icmp_type_ping_request) &
                       (depacketizer.source.code == 0),
                        NextState("RECEIVE")
                    )
                )
            )
        )
        self.comb += [
            depacketizer.source.connect(source, keep={
                "last",
                "msgtype",
                "code",
                "checksum",
                "quench",
                "data",
                "error",
                "be"
            }),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length - icmp_header.length),
        ]
        fsm.act("RECEIVE",
            source.last.eq(depacketizer.source.last | (count >= source.length)),
            If(count >= source.length,
                source.be.eq(depacketizer.source.be & eth_packet_last_mask(dw, source.length)),
            ),
            If(depacketizer.source.last &
               ((count - dw//8 + stream.byte_count(depacketizer.source.be)) < source.length),
                source.error.eq(source.be),
            ),
            If(source.valid & source.ready,
                NextValue(count, count + dw//8),
                If(depacketizer.source.last,
                    NextState("IDLE")
                ).Elif(source.last,
                    NextState("DROP")
                )
            )
        )
        fsm.act("DROP",
            If(depacketizer.source.valid &
               depacketizer.source.last &
               depacketizer.source.ready,
                NextState("IDLE")
            )
        )

# ICMP Echo ----------------------------------------------------------------------------------------

class LiteEthICMPEcho(LiteXModule):
    def __init__(self, dw=8, fifo_depth=128):
        self.sink   = sink   = stream.Endpoint(eth_icmp_user_description(dw))
        self.source = source = stream.Endpoint(eth_icmp_user_description(dw))

        # # #

        assert fifo_depth >= dw//8
        # Store the whole echo before replying. Validate actual byte count as well as the declared
        # length, and discard errored or oversized packets without blocking the receive stream.
        self.buffer = buffer = PacketDropFIFO(eth_icmp_user_description(dw),
            payload_depth = 2**log2_int(max(2, (fifo_depth + dw//8 - 1)//(dw//8)), need_pow2=False),
            param_depth   = 1,
        )
        busy      = Signal()
        receiving = Signal()
        count = Signal(max=fifo_depth + dw//8 + 1)
        size  = Signal.like(count)
        self.comb += [
            sink.connect(buffer.sink, omit={"valid", "ready"}),
            buffer.sink.valid.eq(sink.valid & (~busy | receiving)),
            sink.ready.eq(~busy | receiving),
            size.eq(count + stream.byte_count(sink.be)),
            buffer.discard.eq(
                (sink.length == 0) | (sink.length > fifo_depth) |
                (size > fifo_depth) | ((sink.error & sink.be) != 0) |
                (sink.last & (size != sink.length))),
            buffer.source.connect(source, omit={"checksum"}),
            source.msgtype.eq(icmp_type_ping_reply),
            source.checksum.eq(buffer.source.checksum + 0x800 + (buffer.source.checksum >= 0xf800)),
        ]
        self.sync += [
            If(sink.valid & sink.ready,
                busy.eq(1),
                receiving.eq(~sink.last),
            ),
            If(buffer.drop | (source.valid & source.ready & source.last),
                busy.eq(0),
            ),
        ]
        self.sync += If(sink.valid & sink.ready,
            If(sink.last,
                count.eq(0),
            ).Elif(count <= fifo_depth,
                count.eq(size),
            )
        )

# ICMP ---------------------------------------------------------------------------------------------

class LiteEthICMP(LiteXModule):
    def __init__(self, ip, ip_address, dw=8, fifo_depth=128):
        self.tx   = tx   = LiteEthICMPTX(ip_address, dw)
        self.rx   = rx   = LiteEthICMPRX(ip_address, dw)
        self.echo = echo = LiteEthICMPEcho(dw, fifo_depth=fifo_depth)
        self.comb += [
            rx.source.connect(echo.sink),
            echo.source.connect(tx.sink)
        ]
        ip_port = ip.crossbar.get_port(icmp_protocol, dw)
        self.comb += [
            tx.source.connect(ip_port.sink),
            ip_port.source.connect(rx.sink)
        ]
