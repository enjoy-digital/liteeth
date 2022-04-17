#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from litex.soc.interconnect import stream

from liteeth.packet import Depacketizer, Packetizer

# UDP Crossbar -------------------------------------------------------------------------------------

class LiteEthUDPMasterPort:
    def __init__(self, dw):
        self.dw     = dw
        self.source = stream.Endpoint(eth_udp_user_description(dw))
        self.sink   = stream.Endpoint(eth_udp_user_description(dw))


class LiteEthUDPSlavePort:
    def __init__(self, dw):
        self.dw     = dw
        self.sink   = stream.Endpoint(eth_udp_user_description(dw))
        self.source = stream.Endpoint(eth_udp_user_description(dw))


class LiteEthUDPUserPort(LiteEthUDPSlavePort):
    def __init__(self, dw):
        LiteEthUDPSlavePort.__init__(self, dw)


class LiteEthUDPCrossbar(LiteEthCrossbar):
    def __init__(self, dw=8):
        self.dw = dw
        LiteEthCrossbar.__init__(self, LiteEthUDPMasterPort, "dst_port", dw=dw)

    def get_port(self, udp_port, dw=8, cd="sys"):
        if udp_port in self.users.keys():
            raise ValueError("Port {0:#x} already assigned".format(udp_port))

        user_port     = LiteEthUDPUserPort(dw)
        internal_port = LiteEthUDPUserPort(self.dw)

        # TX
        # ---

        # CDC.
        self.submodules.tx_cdc = tx_cdc = stream.ClockDomainCrossing(
            layout  = eth_udp_user_description(user_port.dw),
            cd_from = cd,
            cd_to   ="sys"
        )
        self.comb += user_port.sink.connect(tx_cdc.sink)

        # Data-Width Conversion.
        self.submodules.tx_converter = tx_converter = stream.StrideConverter(
            description_from = eth_udp_user_description(user_port.dw),
            description_to   = eth_udp_user_description(self.dw)
        )
        self.comb += tx_cdc.source.connect(tx_converter.sink)

        # Interface.
        self.comb += tx_converter.source.connect(internal_port.sink)

        # RX
        # --
        # Data-Width Conversion.
        self.submodules.rx_converter = rx_converter = stream.StrideConverter(
            description_from = eth_udp_user_description(self.dw),
            description_to   = eth_udp_user_description(user_port.dw)
        )
        self.comb += internal_port.source.connect(rx_converter.sink)

        # CDC.
        self.submodules.rx_cdc = rx_cdc = stream.ClockDomainCrossing(
            layout  = eth_udp_user_description(user_port.dw),
            cd_from = "sys",
            cd_to   = cd
        )
        self.comb += rx_converter.source.connect(rx_cdc.sink)

        # Interface.
        self.comb += rx_cdc.source.connect(user_port.source)

        # Expose/Return User Port.
        # ------------------------
        self.users[udp_port] = internal_port

        return user_port

# UDP TX -------------------------------------------------------------------------------------------

class LiteEthUDPPacketizer(Packetizer):
    def __init__(self, dw=8):
        Packetizer.__init__(self,
            eth_udp_description(dw),
            eth_ipv4_user_description(dw),
            udp_header)


class LiteEthUDPTX(Module):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(dw))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(dw))

        # # #

        # Packetizer.
        self.submodules.packetizer = packetizer = LiteEthUDPPacketizer(dw=dw)

        # Data-Path.
        self.comb += [
            sink.connect(packetizer.sink, keep={
                "valid",
                "ready",
                "last",
                "last_be",
                "src_port",
                "dst_port",
                "data"}),
            packetizer.sink.length.eq(sink.length + udp_header.length),
            packetizer.sink.checksum.eq(0), # UDP Checksum is not used, we only rely on MAC CRC.
        ]

        # Control-Path (FSM).
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(packetizer.source.valid,
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.length.eq(packetizer.sink.length),
            source.protocol.eq(udp_protocol),
            source.ip_address.eq(sink.ip_address),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE")
                )
            )
        )

# UDP RX -------------------------------------------------------------------------------------------

class LiteEthUDPDepacketizer(Depacketizer):
    def __init__(self, dw=8):
        Depacketizer.__init__(self,
            eth_ipv4_user_description(dw),
            eth_udp_description(dw),
            udp_header)


class LiteEthUDPRX(Module):
    def __init__(self, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_ipv4_user_description(dw))
        self.source = source = stream.Endpoint(eth_udp_user_description(dw))

        # # #

        # Depacketizer.
        self.submodules.depacketizer = depacketizer = LiteEthUDPDepacketizer(dw)

        # Data-Path.
        self.comb += [
            sink.connect(depacketizer.sink),
            depacketizer.source.connect(source, keep={
                "src_port",
                "dst_port",
                "data",
                "error"}),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(depacketizer.source.length - udp_header.length),
        ]

        # Control-Path (FSM).
        count = Signal(16)
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(count, dw//8),
            If(depacketizer.source.valid,
                NextState("DROP"),
                If(sink.protocol == udp_protocol,
                    NextState("RECEIVE")
                )
            )
        )
        fsm.act("RECEIVE",
            depacketizer.source.connect(source, keep={"valid", "ready"}),
            source.last.eq(depacketizer.source.last | (count >= source.length)),
            If(depacketizer.source.last_be,
               source.last_be.eq(depacketizer.source.last_be),
            ).Elif(
              source.last,
              Case(source.length & (dw//8 - 1), {
                  1         : source.last_be.eq(0b00000001),
                  2         : source.last_be.eq(0b00000010),
                  3         : source.last_be.eq(0b00000100),
                  4         : source.last_be.eq(0b00001000),
                  5         : source.last_be.eq(0b00010000),
                  6         : source.last_be.eq(0b00100000),
                  7         : source.last_be.eq(0b01000000),
                  "default" : source.last_be.eq(2**(dw//8 - 1)),
              })
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
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid &
               depacketizer.source.last &
               depacketizer.source.ready,
                NextState("IDLE")
            )
        )

# UDP ----------------------------------------------------------------------------------------------

class LiteEthUDP(Module):
    def __init__(self, ip, ip_address, dw=8):
        self.submodules.tx = tx = LiteEthUDPTX(ip_address, dw)
        self.submodules.rx = rx = LiteEthUDPRX(ip_address, dw)
        ip_port = ip.crossbar.get_port(udp_protocol, dw)
        self.comb += [
            tx.source.connect(ip_port.sink),
            ip_port.source.connect(rx.sink)
        ]
        self.submodules.crossbar = crossbar = LiteEthUDPCrossbar(dw)
        self.comb += [
            crossbar.master.source.connect(tx.sink),
            rx.source.connect(crossbar.master.sink)
        ]
