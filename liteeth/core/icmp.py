#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

from litex.soc.interconnect.packet import PacketFIFO
from liteeth.packet import Depacketizer, Packetizer

# ICMP TX ------------------------------------------------------------------------------------------

class LiteEthICMPPacketizer(Packetizer):
    def __init__(self, dw=8):
        Packetizer.__init__(self,
            eth_icmp_description(dw),
            eth_ipv4_user_description(dw),
            icmp_header)


class LiteEthICMPTX(Module):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_icmp_user_description(dw))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(dw))

        # # #

        # Packetizer.
        self.submodules.packetizer = packetizer = LiteEthICMPPacketizer(dw)
        self.comb += sink.connect(packetizer.sink, keep={
            "valid",
            "last",
            "ready",
            "msgtype",
            "code",
            "checksum",
            "quench",
            "data",
            "last_be"})

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
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


class LiteEthICMPRX(Module):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_ipv4_user_description(dw))
        self.source = source = stream.Endpoint(eth_icmp_user_description(dw))

        # # #

        # Depacketizer.
        self.submodules.depacketizer = depacketizer = LiteEthICMPDepacketizer(dw)
        self.comb += sink.connect(depacketizer.sink)

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(depacketizer.source.valid,
                NextState("DROP"),
                If(sink.protocol == icmp_protocol,
                    If(depacketizer.source.msgtype == icmp_type_ping_request,
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
                "last_be"}),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length - icmp_header.length),
        ]
        fsm.act("RECEIVE",
            depacketizer.source.connect(source, keep={"valid", "ready"}),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE")
                )
            )
        )
        fsm.act("DROP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid &
               depacketizer.source.last &
               depacketizer.source.ready,
                NextState("IDLE")
            )
        )

# ICMP Echo ----------------------------------------------------------------------------------------

class LiteEthICMPEcho(Module):
    def __init__(self, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_icmp_user_description(dw))
        self.source = source = stream.Endpoint(eth_icmp_user_description(dw))

        # # #

        self.submodules.buffer = PacketFIFO(eth_icmp_user_description(dw),
            payload_depth = 128//(dw//8),
            param_depth   = 1,
            buffered      = True
        )
        self.comb += [
            sink.connect(self.buffer.sink),
            self.buffer.source.connect(source, omit={"checksum"}),
            self.source.msgtype.eq(icmp_type_ping_reply),
            self.source.checksum.eq(self.buffer.source.checksum + 0x800 + (self.buffer.source.checksum >= 0xf800))
        ]

# ICMP ---------------------------------------------------------------------------------------------

class LiteEthICMP(Module):
    def __init__(self, ip, ip_address, dw=8):
        self.submodules.tx   = tx   = LiteEthICMPTX(ip_address, dw)
        self.submodules.rx   = rx   = LiteEthICMPRX(ip_address, dw)
        self.submodules.echo = echo = LiteEthICMPEcho(dw)
        self.comb += [
            rx.source.connect(echo.sink),
            echo.source.connect(tx.sink)
        ]
        ip_port = ip.crossbar.get_port(icmp_protocol, dw)
        self.comb += [
            tx.source.connect(ip_port.sink),
            ip_port.source.connect(rx.sink)
        ]
