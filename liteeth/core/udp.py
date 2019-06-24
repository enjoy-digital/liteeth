# This file is Copyright (c) 2015-2017 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from litex.soc.interconnect import stream
from litex.soc.interconnect.stream_packet import Depacketizer, Packetizer


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

    def get_port(self, udp_port, dw=8, cd="sys"):
        if udp_port in self.users.keys():
            raise ValueError("Port {0:#x} already assigned".format(udp_port))

        user_port = LiteEthUDPUserPort(dw)
        internal_port = LiteEthUDPUserPort(8)

        # tx
        tx_stream = user_port.sink
        if cd is not "sys":
            tx_cdc = stream.AsyncFIFO(eth_udp_user_description(user_port.dw), 4)
            tx_cdc = ClockDomainsRenamer({"write": cd, "read": "sys"})(tx_cdc)
            self.submodules += tx_cdc
            self.comb += tx_stream.connect(tx_cdc.sink)
            tx_stream = tx_cdc.source
        if dw != 8:
            tx_converter = stream.StrideConverter(eth_udp_user_description(user_port.dw),
                                                  eth_udp_user_description(8))
            self.submodules += tx_converter
            self.comb += tx_stream.connect(tx_converter.sink)
            tx_stream = tx_converter.source
        self.comb += tx_stream.connect(internal_port.sink)

        # rx
        rx_stream = internal_port.source
        if dw != 8:
            rx_converter = stream.StrideConverter(eth_udp_user_description(8),
                                                  eth_udp_user_description(user_port.dw))
            self.submodules += rx_converter
            self.comb += rx_stream.connect(rx_converter.sink)
            rx_stream = rx_converter.source
        if cd is not "sys":
            rx_cdc = stream.AsyncFIFO(eth_udp_user_description(user_port.dw), 4)
            rx_cdc = ClockDomainsRenamer({"write": "sys", "read": cd})(rx_cdc)
            self.submodules += rx_cdc
            self.comb += rx_stream.connect(rx_cdc.sink)
            rx_stream = rx_cdc.source
        self.comb += rx_stream.connect(user_port.source)

        self.users[udp_port] = internal_port

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
            packetizer.sink.valid.eq(sink.valid),
            packetizer.sink.last.eq(sink.last),
            sink.ready.eq(packetizer.sink.ready),
            packetizer.sink.src_port.eq(sink.src_port),
            packetizer.sink.dst_port.eq(sink.dst_port),
            packetizer.sink.length.eq(sink.length + udp_header.length),
            packetizer.sink.checksum.eq(0),  # Disabled (MAC CRC is enough)
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
            source.length.eq(packetizer.sink.length),
            source.protocol.eq(udp_protocol),
            source.ip_address.eq(sink.ip_address),
            If(source.valid & source.last & source.ready,
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
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                depacketizer.source.ready.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal(reset_less=True)
        self.sync += valid.eq(
            depacketizer.source.valid &
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
            source.last.eq(depacketizer.source.last),
            source.src_port.eq(depacketizer.source.src_port),
            source.dst_port.eq(depacketizer.source.dst_port),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(depacketizer.source.length - udp_header.length),
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
