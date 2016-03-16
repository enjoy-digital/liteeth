from liteeth.common import *

from litex.soc.interconnect.stream_packet import Depacketizer, Packetizer


# icmp tx

class LiteEthICMPPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_icmp_description(8),
            eth_ipv4_user_description(8),
            icmp_header)


class LiteEthICMPTX(Module):
    def __init__(self, ip_address):
        self.sink = sink = stream.Endpoint(eth_icmp_user_description(8))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(8))

        # # #

        self.submodules.packetizer = packetizer = LiteEthICMPPacketizer()
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

# icmp rx

class LiteEthICMPDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_ipv4_user_description(8),
            eth_icmp_description(8),
            icmp_header)


class LiteEthICMPRX(Module):
    def __init__(self, ip_address):
        self.sink = sink = stream.Endpoint(eth_ipv4_user_description(8))
        self.source = source = stream.Endpoint(eth_icmp_user_description(8))

        # # #

        self.submodules.depacketizer = depacketizer = LiteEthICMPDepacketizer()
        self.comb += sink.connect(depacketizer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                depacketizer.source.ready.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal()
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

# icmp echo

class LiteEthICMPEcho(Module):
    def __init__(self):
        self.sink = sink = stream.Endpoint(eth_icmp_user_description(8))
        self.source = source = stream.Endpoint(eth_icmp_user_description(8))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        self.submodules.buffer = stream.SyncFIFO(eth_icmp_user_description(8), 128)
        self.comb += [
            sink.connect(self.buffer.sink),
            self.buffer.source.connect(source),
            self.source.msgtype.eq(0x0),
            self.source.checksum.eq(~((~self.buffer.source.checksum)-0x0800))
        ]

# icmp

class LiteEthICMP(Module):
    def __init__(self, ip, ip_address):
        self.submodules.tx = tx = LiteEthICMPTX(ip_address)
        self.submodules.rx = rx = LiteEthICMPRX(ip_address)
        self.submodules.echo = echo = LiteEthICMPEcho()
        self.comb += [
            rx.source.connect(echo.sink),
            echo.source.connect(tx.sink)
        ]
        ip_port = ip.crossbar.get_port(icmp_protocol)
        self.comb += [
            tx.source.connect(ip_port.sink),
            ip_port.source.connect(rx.sink)
        ]
