#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

from litex.soc.interconnect.packet import Depacketizer, Packetizer

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

        self.submodules.packetizer = packetizer = LiteEthICMPPacketizer(dw)
        self.comb += [
            packetizer.sink.valid.eq(sink.valid),
            packetizer.sink.last.eq(sink.last),
            sink.ready.eq(packetizer.sink.ready),
            packetizer.sink.msgtype.eq(sink.msgtype),
            packetizer.sink.code.eq(sink.code),
            packetizer.sink.checksum.eq(sink.checksum),
            packetizer.sink.quench.eq(sink.quench),
            packetizer.sink.data.eq(sink.data)
        ]

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            packetizer.source.ready.eq(1),
            If(packetizer.source.valid,
                packetizer.source.ready.eq(0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.length.eq(sink.length + icmp_header.length),
            source.protocol.eq(icmp_protocol),
            source.ip_address.eq(sink.ip_address),
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

        self.submodules.depacketizer = depacketizer = LiteEthICMPDepacketizer(dw)
        self.comb += sink.connect(depacketizer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                depacketizer.source.ready.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal(reset_less=True)
        self.sync += valid.eq(
            depacketizer.source.valid &
            (sink.protocol == icmp_protocol)
        )
        fsm.act("CHECK",
            If(valid,
                NextState("PRESENT")
            ).Else(
                NextState("DROP")
            )
        )
        self.comb += [
            source.last.eq(depacketizer.source.last),
            source.msgtype.eq(depacketizer.source.msgtype),
            source.code.eq(depacketizer.source.code),
            source.checksum.eq(depacketizer.source.checksum),
            source.quench.eq(depacketizer.source.quench),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length - icmp_header.length),
            source.data.eq(depacketizer.source.data),
            source.error.eq(depacketizer.source.error)
        ]
        fsm.act("PRESENT",
            source.valid.eq(depacketizer.source.valid),
            depacketizer.source.ready.eq(source.ready),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
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

        # TODO: optimize ressources (no need to store parameters as datas)
        self.submodules.buffer = stream.SyncFIFO(eth_icmp_user_description(dw), 128//(dw//8), buffered=True)
        self.comb += [
            sink.connect(self.buffer.sink),
            self.buffer.source.connect(source),
            self.source.msgtype.eq(0x0),
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
