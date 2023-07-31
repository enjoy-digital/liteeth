#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from liteeth.common import *
from liteeth.packet import Depacketizer, Packetizer

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
            arp_header
        )


class LiteEthARPTX(LiteXModule):
    def __init__(self, mac_address, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(_arp_table_layout)
        self.source = source = stream.Endpoint(eth_mac_description(dw))

        # # #

        packet_length = max(arp_header.length, arp_min_length)
        packet_words  = packet_length//(dw//8)
        counter       = Signal(max=packet_words, reset_less=True)

        self.packetizer = packetizer = LiteEthARPPacketizer(dw)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(counter, 0),
            If(sink.valid,
                NextState("SEND")
            )
        )
        self.comb += [
            packetizer.sink.last.eq(counter == (packet_words - 1)),
            If(packetizer.sink.last,
                packetizer.sink.last_be.eq(1 if len(packetizer.sink.last_be) == 1 else
                                           2**(packet_length % (dw // 8) - 1)
                ),
            ),
            packetizer.sink.hwtype.eq(arp_hwtype_ethernet),
            packetizer.sink.proto.eq(arp_proto_ip),
            packetizer.sink.hwsize.eq(6),
            packetizer.sink.protosize.eq(4),
            packetizer.sink.sender_mac.eq(mac_address),
            packetizer.sink.sender_ip.eq(ip_address),
            packetizer.sink.target_ip.eq(sink.ip_address),
            If(sink.reply,
                packetizer.sink.opcode.eq(arp_opcode_reply),
                packetizer.sink.target_mac.eq(sink.mac_address),
            ).Elif(sink.request,
                packetizer.sink.opcode.eq(arp_opcode_request),
                packetizer.sink.target_mac.eq(bcast_mac_address),
            )
        ]
        self.comb += [
            packetizer.source.connect(source, omit={"valid", "ready"}),
            source.target_mac.eq(packetizer.sink.target_mac),
            source.sender_mac.eq(mac_address),
            source.ethernet_type.eq(ethernet_type_arp),
        ]
        fsm.act("SEND",
            packetizer.sink.valid.eq(1),
            packetizer.source.connect(source, keep={"valid", "ready"}),
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
            arp_header
        )


class LiteEthARPRX(LiteXModule):
    def __init__(self, mac_address, ip_address, dw=8):
        self.sink   = sink   = stream.Endpoint(eth_mac_description(dw))
        self.source = source = stream.Endpoint(_arp_table_layout)

        # # #

        self.depacketizer = depacketizer = LiteEthARPDepacketizer(dw)
        self.comb += sink.connect(depacketizer.sink)

        self.fsm = fsm = FSM(reset_state="IDLE")
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
            (depacketizer.source.hwtype    == arp_hwtype_ethernet) &
            (depacketizer.source.proto     == arp_proto_ip) &
            (depacketizer.source.hwsize    == 6) &
            (depacketizer.source.protosize == 4) &
            (depacketizer.source.target_ip == ip_address)
        )
        reply   = Signal()
        request = Signal()
        self.comb += Case(depacketizer.source.opcode, {
            arp_opcode_request : [request.eq(1)],
            arp_opcode_reply   : [reply.eq(1)],
            "default"          : []
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

# ARP Cache ----------------------------------------------------------------------------------------

class LiteEthARPCache(Module):
    def __init__(self, entries, clk_freq):
        # Update interface.
        self.update = stream.Endpoint([("ip_address", 32), ("mac_address", 48)])

        # Request/Response interface.
        self.request  = stream.Endpoint([("ip_address", 32)])
        self.response = stream.Endpoint([("mac_address", 48), ("error", 1)])

        # # #

        entries = max(entries, 2)

        mem_width   = 32 + 48 + 1 # IP + MAC + Valid.
        mem         = Memory(mem_width, entries)
        mem_wr_port = mem.get_port(write_capable=True)
        mem_rd_port = mem.get_port(async_read=True) # FIXME: Avoid async_read.
        self.specials += mem, mem_wr_port, mem_rd_port

        update_count = Signal(max=entries)
        search_count = Signal(max=entries)
        error        = Signal()

        mem_wr_port_valid       = mem_wr_port.dat_w[80]
        mem_wr_port_ip_address  = mem_wr_port.dat_w[0:32]
        mem_wr_port_mac_address = mem_wr_port.dat_w[32:80]

        mem_rd_port_valid       = mem_rd_port.dat_r[80]
        mem_rd_port_ip_address  = mem_rd_port.dat_r[0:32]
        mem_rd_port_mac_address = mem_rd_port.dat_r[32:80]

        self.submodules.fsm = fsm = FSM(reset_state="CLEAR")
        fsm.act("CLEAR",
            mem_wr_port.we.eq(1),
            mem_wr_port.adr.eq(update_count),
            mem_wr_port_valid.eq(0),
            NextValue(update_count, update_count + 1),
            If(update_count == (entries - 1),
                NextState("IDLE")
            )
        )
        fsm.act("IDLE",
            If(self.update.valid,
                NextState("MEM_UPDATE")
            ),
            If(self.request.valid,
                NextValue(search_count, 0),
                NextState("MEM_SEARCH")
            )
        )
        fsm.act("MEM_UPDATE",
            mem_wr_port.we.eq(1),
            mem_wr_port.adr.eq(update_count),
            mem_wr_port_valid.eq(1),
            mem_wr_port_ip_address.eq( self.update.ip_address),
            mem_wr_port_mac_address.eq(self.update.mac_address),
            self.update.ready.eq(1),
            If(update_count == (entries - 1),
                NextValue(update_count, 0)
            ).Else(
                NextValue(update_count, update_count + 1)
            ),
            NextState("IDLE")
        )
        fsm.act("MEM_SEARCH",
           mem_rd_port.adr.eq(search_count),
           If(mem_rd_port_valid & (mem_rd_port_ip_address == self.request.ip_address),
               NextValue(error, 0),
               NextState("RESPONSE")
           ).Elif(search_count == (entries - 1),
               NextValue(error, 1),
               NextState("RESPONSE")
           ).Else(
               NextValue(search_count, search_count + 1)
           )
        )
        fsm.act("RESPONSE",
           self.request.ready.eq(1),
           self.response.valid.eq(1),
           self.response.error.eq(error),
           self.response.mac_address.eq(mem_rd_port_mac_address),
           NextState("IDLE")
       )

# ARP Table ----------------------------------------------------------------------------------------

class LiteEthARPTable(LiteXModule):
    def __init__(self, clk_freq, entries=1, max_requests=8):
        self.sink   = sink   = stream.Endpoint(_arp_table_layout)  # from arp_rx
        self.source = source = stream.Endpoint(_arp_table_layout)  # to arp_tx

        # Request/Response interface
        self.request  = request  = stream.Endpoint(arp_table_request_layout)
        self.response = response = stream.Endpoint(arp_table_response_layout)

        # # #

        request_pending    = Signal()
        request_counter    = Signal(max=max_requests)
        request_ip_address = Signal(32, reset_less=True)

        self.request_timer = WaitTimer(100e-3*clk_freq)
        self.comb += self.request_timer.wait.eq(request_pending & ~self.request_timer.done)

        self.cache = cache = LiteEthARPCache(entries=entries, clk_freq=clk_freq)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            # Note: for simplicicy, if ARP table is busy response from arp_rx is lost. This is
            # compensated by the protocol (retries)
            If(sink.valid & sink.request,
                NextState("SEND_REPLY")
            ).Elif(sink.valid & sink.reply,
                NextState("UPDATE_TABLE"),
            ).Elif(request.valid,
                NextState("CHECK_TABLE")
            ).Elif(self.request_timer.done,
                NextState("CHECK_REQUEST")
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
            If(request_pending & (request_ip_address == sink.ip_address),
                cache.update.valid.eq(1),
                cache.update.ip_address.eq(sink.ip_address),
                cache.update.mac_address.eq(sink.mac_address),
                If(cache.update.ready,
                    NextValue(request_pending, 0),
                    NextState("PRESENT_RESPONSE")
                )
            ).Else(
                NextState("IDLE")
            )
        )
        fsm.act("CHECK_REQUEST",
            If(request_counter == (max_requests - 1),
                NextValue(response.failed, 1),
                NextValue(request_counter, 0),
                NextValue(request_pending, 0),
                NextState("PRESENT_RESPONSE")
            ).Else(
                NextState("SEND_REQUEST")
            )
        )
        fsm.act("CHECK_TABLE",
            cache.request.valid.eq(1),
            cache.request.ip_address.eq(request.ip_address),
            If(cache.response.valid,
                request.ready.eq(1),
                If(cache.response.error,
                    NextValue(request_counter, 0),
                    NextValue(request_pending, 1),
                    NextValue(request_ip_address, request.ip_address),
                    NextState("SEND_REQUEST")
                ).Else(
                    NextValue(response.mac_address, cache.response.mac_address),
                    NextState("PRESENT_RESPONSE"),
                )
            )
        )
        fsm.act("SEND_REQUEST",
            source.valid.eq(1),
            source.request.eq(1),
            source.ip_address.eq(request_ip_address),
            If(source.ready,
                NextValue(request_counter, request_counter + 1),
                NextState("IDLE")
            )
        )
        fsm.act("PRESENT_RESPONSE",
            response.valid.eq(1),
            If(response.ready,
                NextValue(response.failed, 0),
                NextState("IDLE")
            )
        )

# ARP ----------------------------------------------------------------------------------------------

class LiteEthARP(LiteXModule):
    def __init__(self, mac, mac_address, ip_address, clk_freq, entries=1, dw=8):
        self.tx    = tx    = LiteEthARPTX(mac_address, ip_address, dw)
        self.rx    = rx    = LiteEthARPRX(mac_address, ip_address, dw)
        self.table = table = LiteEthARPTable(clk_freq, entries=entries)
        self.comb += [
            rx.source.connect(table.sink),
            table.source.connect(tx.sink)
        ]
        mac_port = mac.crossbar.get_port(ethernet_type_arp, dw=dw)
        self.comb += [
            tx.source.connect(mac_port.sink),
            mac_port.source.connect(rx.sink)
        ]
