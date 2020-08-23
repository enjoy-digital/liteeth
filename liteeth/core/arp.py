#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

from migen.genlib.misc import WaitTimer

from litex.soc.interconnect.packet import Depacketizer, Packetizer

# ARP Layouts --------------------------------------------------------------------------------------

_arp_table_layout = [
        ("reply",        1),
        ("request",      1),
        ("ip_address",  32),
        ("mac_address", 48)
    ]

# ARP TX -------------------------------------------------------------------------------------------

class LiteEthARPPacketizer(Packetizer):
    def __init__(self, dw=8):
        Packetizer.__init__(self,
            eth_arp_description(dw),
            eth_mac_description(dw),
            arp_header)


class LiteEthARPTX(Module):
    def __init__(self, mac_address, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(_arp_table_layout)
        self.source = source = stream.Endpoint(eth_mac_description(dw))

        # # #

        counter = Signal(max=max(arp_header.length, eth_min_len), reset_less=True)

        self.submodules.packetizer = packetizer = LiteEthARPPacketizer(dw)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            NextValue(counter, 0),
            If(sink.valid,
                sink.ready.eq(0),
                NextState("SEND")
            )
        )
        self.comb += [
            packetizer.sink.last.eq(counter == max(arp_header.length, eth_min_len)-1),
            packetizer.sink.hwtype.eq(arp_hwtype_ethernet),
            packetizer.sink.proto.eq(arp_proto_ip),
            packetizer.sink.hwsize.eq(6),
            packetizer.sink.protosize.eq(4),
            packetizer.sink.sender_mac.eq(mac_address),
            packetizer.sink.sender_ip.eq(ip_address),
            If(sink.reply,
                packetizer.sink.opcode.eq(arp_opcode_reply),
                packetizer.sink.target_mac.eq(sink.mac_address),
                packetizer.sink.target_ip.eq(sink.ip_address)
            ).Elif(sink.request,
                packetizer.sink.opcode.eq(arp_opcode_request),
                packetizer.sink.target_mac.eq(0xffffffffffff),
                packetizer.sink.target_ip.eq(sink.ip_address)
            )
        ]
        fsm.act("SEND",
            packetizer.sink.valid.eq(1),
            packetizer.source.connect(source),
            source.target_mac.eq(packetizer.sink.target_mac),
            source.sender_mac.eq(mac_address),
            source.ethernet_type.eq(ethernet_type_arp),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(source.last,
                    sink.ready.eq(1),
                    NextState("IDLE")
                )
            )
        )

# ARP RX -------------------------------------------------------------------------------------------

class LiteEthARPDepacketizer(Depacketizer):
    def __init__(self, dw=8):
        Depacketizer.__init__(self,
            eth_mac_description(dw),
            eth_arp_description(dw),
            arp_header)


class LiteEthARPRX(Module):
    def __init__(self, mac_address, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_mac_description(dw))
        self.source = source = stream.Endpoint(_arp_table_layout)

        # # #s

        self.submodules.depacketizer = depacketizer = LiteEthARPDepacketizer(dw)
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
            (depacketizer.source.hwtype == arp_hwtype_ethernet) &
            (depacketizer.source.proto == arp_proto_ip) &
            (depacketizer.source.hwsize == 6) &
            (depacketizer.source.protosize == 4) &
            (depacketizer.source.target_ip == ip_address)
        )
        reply = Signal()
        request = Signal()
        self.comb += Case(depacketizer.source.opcode, {
            arp_opcode_request: [request.eq(1)],
            arp_opcode_reply:   [reply.eq(1)],
            "default":          []
            })
        self.comb += [
            source.ip_address.eq(depacketizer.source.sender_ip),
            source.mac_address.eq(depacketizer.source.sender_mac)
        ]
        fsm.act("CHECK",
            If(valid,
                source.valid.eq(1),
                source.reply.eq(reply),
                source.request.eq(request)
            ),
            NextState("TERMINATE")
        ),
        fsm.act("TERMINATE",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid & depacketizer.source.last,
                NextState("IDLE")
            )
        )

# ARP Table ----------------------------------------------------------------------------------------

class LiteEthARPTable(Module):
    def __init__(self, clk_freq, max_requests=8):
        self.sink   = sink   = stream.Endpoint(_arp_table_layout)  # from arp_rx
        self.source = source = stream.Endpoint(_arp_table_layout)  # to arp_tx

        # Request/Response interface
        self.request  = request  = stream.Endpoint(arp_table_request_layout)
        self.response = response = stream.Endpoint(arp_table_response_layout)

        # # #

        request_pending     = Signal()
        request_pending_clr = Signal()
        request_pending_set = Signal()
        self.sync += \
            If(request_pending_clr,
                request_pending.eq(0)
            ).Elif(request_pending_set,
                request_pending.eq(1)
            )

        request_ip_address        = Signal(32, reset_less=True)
        request_ip_address_reset  = Signal()
        request_ip_address_update = Signal()
        self.sync += \
            If(request_ip_address_reset,
                request_ip_address.eq(0)
            ).Elif(request_ip_address_update,
                request_ip_address.eq(request.ip_address)
            )

        request_timer = WaitTimer(clk_freq//10)
        self.submodules += request_timer
        request_counter       = Signal(max=max_requests)
        request_counter_reset = Signal()
        request_counter_ce    = Signal()
        self.sync += \
            If(request_counter_reset,
                request_counter.eq(0)
            ).Elif(request_counter_ce,
                request_counter.eq(request_counter + 1)
            )
        self.comb += request_timer.wait.eq(request_pending & ~request_counter_ce)

        # Note: Store only 1 IP/MAC couple, can be improved with a real
        # table in the future to improve performance when packets are
        # targeting multiple destinations.
        update = Signal()
        cached_valid       = Signal()
        cached_ip_address  = Signal(32, reset_less=True)
        cached_mac_address = Signal(48, reset_less=True)
        cached_timer       = WaitTimer(clk_freq*10)
        self.submodules += cached_timer

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            # Note: for simplicicy, if ARP table is busy response from arp_rx
            # is lost. This is compensated by the protocol (retries)
            If(sink.valid & sink.request,
                NextState("SEND_REPLY")
            ).Elif(sink.valid & sink.reply & request_pending,
                NextState("UPDATE_TABLE"),
            ).Elif(request_counter == max_requests-1,
                NextState("PRESENT_RESPONSE")
            ).Elif(request.valid | (request_pending & request_timer.done),
                NextState("CHECK_TABLE")
            )
        )
        fsm.act("SEND_REPLY",
            source.valid.eq(1),
            source.reply.eq(1),
            source.ip_address.eq(sink.ip_address),
            source.mac_address.eq(sink.mac_address),
            If(source.ready,
                NextState("IDLE")
            )
        )
        fsm.act("UPDATE_TABLE",
            request_pending_clr.eq(1),
            update.eq(1),
            NextState("CHECK_TABLE")
        )
        self.sync += \
            If(update,
                cached_valid.eq(1),
                cached_ip_address.eq(sink.ip_address),
                cached_mac_address.eq(sink.mac_address),
            ).Else(
                If(cached_timer.done,
                    cached_valid.eq(0)
                )
            )
        self.comb += cached_timer.wait.eq(~update)
        fsm.act("CHECK_TABLE",
            If(cached_valid,
                If(request_ip_address == cached_ip_address,
                    request_ip_address_reset.eq(1),
                    NextState("PRESENT_RESPONSE"),
                ).Elif(request.ip_address == cached_ip_address,
                    request.ready.eq(request.valid),
                    NextState("PRESENT_RESPONSE"),
                ).Else(
                    request_ip_address_update.eq(request.valid),
                    NextState("SEND_REQUEST")
                )
            ).Else(
                request_ip_address_update.eq(request.valid),
                NextState("SEND_REQUEST")
            )
        )
        fsm.act("SEND_REQUEST",
            source.valid.eq(1),
            source.request.eq(1),
            source.ip_address.eq(request_ip_address),
            If(source.ready,
                request_counter_reset.eq(request.valid),
                request_counter_ce.eq(1),
                request_pending_set.eq(1),
                request.ready.eq(1),
                NextState("IDLE")
            )
        )
        self.comb += [
            If(request_counter == max_requests - 1,
                response.failed.eq(1),
                request_counter_reset.eq(1),
                request_pending_clr.eq(1)
            ),
            response.mac_address.eq(cached_mac_address)
        ]
        fsm.act("PRESENT_RESPONSE",
            response.valid.eq(1),
            If(response.ready,
                NextState("IDLE")
            )
        )

# ARP ----------------------------------------------------------------------------------------------

class LiteEthARP(Module):
    def __init__(self, mac, mac_address, ip_address, clk_freq, dw=8):
        self.submodules.tx    = tx    = LiteEthARPTX(mac_address, ip_address, dw)
        self.submodules.rx    = rx    = LiteEthARPRX(mac_address, ip_address, dw)
        self.submodules.table = table = LiteEthARPTable(clk_freq)
        self.comb += [
            rx.source.connect(table.sink),
            table.source.connect(tx.sink)
        ]
        mac_port = mac.crossbar.get_port(ethernet_type_arp, dw=dw)
        self.comb += [
            tx.source.connect(mac_port.sink),
            mac_port.source.connect(rx.sink)
        ]
