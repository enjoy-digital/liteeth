from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from litex.soc.interconnect.stream_packet import Depacketizer, Packetizer, Buffer


# udp crossbar

class LiteEthUDPMasterPort:
    def __init__(self, dw):
        self.dw = dw
        self.source = stream.Endpoint(eth_udp_user_description(dw))
        self.sink = stream.Endpoint(eth_udp_user_description(dw))


class LiteEthUDPSlavePort:
    def __init__(self, dw):
        self.dw = dw
        self.sink = stream.Endpoint(eth_udp_user_description(dw))
        self.source = stream.Endpoint(eth_udp_user_description(dw))


class LiteEthUDPUserPort(LiteEthUDPSlavePort):
    def __init__(self, dw):
        LiteEthUDPSlavePort.__init__(self, dw)


class LiteEthUDPCrossbar(LiteEthCrossbar):
    def __init__(self):
        LiteEthCrossbar.__init__(self, LiteEthUDPMasterPort, "dst_port")

    def get_port(self, udp_port, dw=8):
        if udp_port in self.users.keys():
            raise ValueError("Port {0:#x} already assigned".format(udp_port))
        user_port = LiteEthUDPUserPort(dw)
        internal_port = LiteEthUDPUserPort(8)
        if dw != 8:
            converter = stream.StrideConverter(eth_udp_user_description(user_port.dw),
                                               eth_udp_user_description(8))
            self.submodules += converter
            self.comb += [
                user_port.sink.connect(converter.sink),
                converter.source.connect(internal_port.sink)
            ]
            converter = stream.StrideConverter(eth_udp_user_description(8),
                                               eth_udp_user_description(user_port.dw))
            self.submodules += converter
            self.comb += [
                internal_port.source.connect(converter.sink),
                converter.source.connect(user_port.source)
            ]
            self.users[udp_port] = internal_port
        else:
            self.users[udp_port] = user_port
        return user_port

# udp tx

class LiteEthUDPPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_udp_description(8),
            eth_ipv4_user_description(8),
            udp_header)


class LiteEthUDPTX(Module):
    def __init__(self, ip_address):
        self.sink = sink = stream.Endpoint(eth_udp_user_description(8))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(8))

        # # #

        self.submodules.packetizer = packetizer = LiteEthUDPPacketizer()
        self.comb += [
            packetizer.sink.stb.eq(sink.stb),
            packetizer.sink.eop.eq(sink.eop),
            sink.ack.eq(packetizer.sink.ack),
            packetizer.sink.src_port.eq(sink.src_port),
            packetizer.sink.dst_port.eq(sink.dst_port),
            packetizer.sink.length.eq(sink.length + udp_header.length),
            packetizer.sink.checksum.eq(0),  # Disabled (MAC CRC is enough)
            packetizer.sink.data.eq(sink.data)
        ]

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            packetizer.source.ack.eq(1),
            If(packetizer.source.stb,
                packetizer.source.ack.eq(0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.length.eq(packetizer.sink.length),
            source.protocol.eq(udp_protocol),
            source.ip_address.eq(sink.ip_address),
            If(source.stb & source.eop & source.ack,
                NextState("IDLE")
            )
        )

# udp rx

class LiteEthUDPDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_ipv4_user_description(8),
            eth_udp_description(8),
            udp_header)


class LiteEthUDPRX(Module):
    def __init__(self, ip_address):
        self.sink = sink = stream.Endpoint(eth_ipv4_user_description(8))
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # # #

        self.submodules.depacketizer = depacketizer = LiteEthUDPDepacketizer()
        self.comb += sink.connect(depacketizer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            depacketizer.source.ack.eq(1),
            If(depacketizer.source.stb,
                depacketizer.source.ack.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal()
        self.sync += valid.eq(
            depacketizer.source.stb &
            (sink.protocol == udp_protocol)
        )

        fsm.act("CHECK",
            If(valid,
                NextState("PRESENT")
            ).Else(
                NextState("DROP")
            )
        )
        self.comb += [
            source.eop.eq(depacketizer.source.eop),
            source.src_port.eq(depacketizer.source.src_port),
            source.dst_port.eq(depacketizer.source.dst_port),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(depacketizer.source.length - udp_header.length),
            source.data.eq(depacketizer.source.data),
            source.error.eq(depacketizer.source.error)
        ]
        fsm.act("PRESENT",
            source.stb.eq(depacketizer.source.stb),
            depacketizer.source.ack.eq(source.ack),
            If(source.stb & source.eop & source.ack,
                NextState("IDLE")
            )
        )
        fsm.act("DROP",
            depacketizer.source.ack.eq(1),
            If(depacketizer.source.stb &
               depacketizer.source.eop &
               depacketizer.source.ack,
                NextState("IDLE")
            )
        )

# udp

class LiteEthUDP(Module):
    def __init__(self, ip, ip_address):
        self.submodules.tx = tx = LiteEthUDPTX(ip_address)
        self.submodules.rx = rx = LiteEthUDPRX(ip_address)
        ip_port = ip.crossbar.get_port(udp_protocol)
        self.comb += [
            tx.source.connect(ip_port.sink),
            ip_port.source.connect(rx.sink)
        ]
        self.submodules.crossbar = crossbar = LiteEthUDPCrossbar()
        self.comb += [
            crossbar.master.source.connect(tx.sink),
            rx.source.connect(crossbar.master.sink)
        ]
