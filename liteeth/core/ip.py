# This file is Copyright (c) 2015-2017 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from litex.soc.interconnect.stream_packet import Depacketizer, Packetizer


# ip crossbar

class LiteEthIPV4MasterPort:
    def __init__(self, dw):
        self.dw = dw
        self.source = stream.Endpoint(eth_ipv4_user_description(dw))
        self.sink = stream.Endpoint(eth_ipv4_user_description(dw))


class LiteEthIPV4SlavePort:
    def __init__(self, dw):
        self.dw = dw
        self.sink = stream.Endpoint(eth_ipv4_user_description(dw))
        self.source = stream.Endpoint(eth_ipv4_user_description(dw))


class LiteEthIPV4UserPort(LiteEthIPV4SlavePort):
    def __init__(self, dw):
        LiteEthIPV4SlavePort.__init__(self, dw)


class LiteEthIPV4Crossbar(LiteEthCrossbar):
    def __init__(self):
        LiteEthCrossbar.__init__(self, LiteEthIPV4MasterPort, "protocol")

    def get_port(self, protocol):
        if protocol in self.users.keys():
            raise ValueError("Protocol {0:#x} already assigned".format(protocol))
        port = LiteEthIPV4UserPort(8)
        self.users[protocol] = port
        return port

# ip checksum

@ResetInserter()
@CEInserter()
class LiteEthIPV4Checksum(Module):
    def __init__(self, words_per_clock_cycle=1, skip_checksum=False):
        self.header = Signal(ipv4_header.length*8)
        self.value = Signal(16)
        self.done = Signal()

        # # #

        s = Signal(17, reset_less=True)
        r = Signal(17, reset_less=True)
        n_cycles = 0
        for i in range(ipv4_header.length//2):
            if skip_checksum and (i == ipv4_header.fields["checksum"].byte//2):
                pass
            else:
                s_next = Signal(17, reset_less=True)
                r_next = Signal(17, reset_less=True)
                self.comb += s_next.eq(r + self.header[i*16:(i+1)*16])
                r_next_eq = r_next.eq(Cat(s_next[:16]+s_next[16], Signal()))
                if (i%words_per_clock_cycle) != 0:
                    self.comb += r_next_eq
                else:
                    self.sync += If(~self.done, r_next_eq)
                    n_cycles += 1
                s, r = s_next, r_next
        self.comb += self.value.eq(~Cat(r[8:16], r[:8]))

        if not skip_checksum:
            n_cycles += 1
        counter = Signal(max=n_cycles+1)
        counter_ce = Signal()
        self.sync += If(counter_ce, counter.eq(counter + 1))

        self.comb += [
            counter_ce.eq(~self.done),
            self.done.eq(counter == n_cycles)
        ]

# ip tx

class LiteEthIPV4Packetizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_ipv4_description(8),
            eth_mac_description(8),
            ipv4_header)


class LiteEthIPTX(Module):
    def __init__(self, mac_address, ip_address, arp_table):
        self.sink = sink = stream.Endpoint(eth_ipv4_user_description(8))
        self.source = source = stream.Endpoint(eth_mac_description(8))
        self.target_unreachable = Signal()

        # # #

        self.submodules.checksum = checksum = LiteEthIPV4Checksum(skip_checksum=True)
        self.comb += [
            checksum.ce.eq(sink.valid),
            checksum.reset.eq(source.valid & source.last & source.ready)
        ]

        self.submodules.packetizer = packetizer = LiteEthIPV4Packetizer()
        self.comb += [
            packetizer.sink.valid.eq(sink.valid & checksum.done),
            packetizer.sink.last.eq(sink.last),
            sink.ready.eq(packetizer.sink.ready & checksum.done),
            packetizer.sink.target_ip.eq(sink.ip_address),
            packetizer.sink.protocol.eq(sink.protocol),
            packetizer.sink.total_length.eq(sink.length + (0x5*4)),
            packetizer.sink.version.eq(0x4),     # ipv4
            packetizer.sink.ihl.eq(0x5),         # 20 bytes
            packetizer.sink.identification.eq(0),
            packetizer.sink.ttl.eq(0x80),
            packetizer.sink.sender_ip.eq(ip_address),
            packetizer.sink.data.eq(sink.data),
            checksum.header.eq(packetizer.header),
            packetizer.sink.checksum.eq(checksum.value)
        ]

        target_mac = Signal(48, reset_less=True)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            packetizer.source.ready.eq(1),
            If(packetizer.source.valid,
                packetizer.source.ready.eq(0),
                NextState("SEND_MAC_ADDRESS_REQUEST")
            )
        )
        self.comb += arp_table.request.ip_address.eq(sink.ip_address)
        fsm.act("SEND_MAC_ADDRESS_REQUEST",
            arp_table.request.valid.eq(1),
            If(arp_table.request.valid & arp_table.request.ready,
                NextState("WAIT_MAC_ADDRESS_RESPONSE")
            )
        )
        fsm.act("WAIT_MAC_ADDRESS_RESPONSE",
            If(arp_table.response.valid,
                arp_table.response.ready.eq(1),
                If(arp_table.response.failed,
                    self.target_unreachable.eq(1),
                    NextState("DROP"),
                ).Else(
                    NextState("SEND")
                )
            )
        )
        self.sync += \
            If(arp_table.response.valid,
                target_mac.eq(arp_table.response.mac_address)
            )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.ethernet_type.eq(ethernet_type_ip),
            source.target_mac.eq(target_mac),
            source.sender_mac.eq(mac_address),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )
        fsm.act("DROP",
            packetizer.source.ready.eq(1),
            If(packetizer.source.valid &
               packetizer.source.last &
               packetizer.source.ready,
                NextState("IDLE")
            )
        )

# ip rx

class LiteEthIPV4Depacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_mac_description(8),
            eth_ipv4_description(8),
            ipv4_header)


class LiteEthIPRX(Module):
    def __init__(self, mac_address, ip_address):
        self.sink = sink = stream.Endpoint(eth_mac_description(8))
        self.source = source = stream.Endpoint(eth_ipv4_user_description(8))

        # # #

        self.submodules.depacketizer = depacketizer = LiteEthIPV4Depacketizer()
        self.comb += sink.connect(depacketizer.sink)

        self.submodules.checksum = checksum = LiteEthIPV4Checksum(skip_checksum=False)
        self.comb += [
            checksum.header.eq(depacketizer.header),
            checksum.reset.eq(~(depacketizer.source.valid)),
            checksum.ce.eq(1)
        ]

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
            (depacketizer.source.target_ip == ip_address) &
            (depacketizer.source.version == 0x4) &
            (depacketizer.source.ihl == 0x5) &
            (checksum.value == 0)
        )

        fsm.act("CHECK",
            If(checksum.done,
                If(valid,
                    NextState("PRESENT")
                ).Else(
                    NextState("DROP")
                )
            )
        )
        self.comb += [
            source.last.eq(depacketizer.source.last),
            source.length.eq(depacketizer.source.total_length - (0x5*4)),
            source.protocol.eq(depacketizer.source.protocol),
            source.ip_address.eq(depacketizer.source.sender_ip),
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

# ip

class LiteEthIP(Module):
    def __init__(self, mac, mac_address, ip_address, arp_table):
        self.submodules.tx = tx = LiteEthIPTX(mac_address, ip_address, arp_table)
        self.submodules.rx = rx = LiteEthIPRX(mac_address, ip_address)
        mac_port = mac.crossbar.get_port(ethernet_type_ip)
        self.comb += [
            tx.source.connect(mac_port.sink),
            mac_port.source.connect(rx.sink)
        ]
        self.submodules.crossbar = crossbar = LiteEthIPV4Crossbar()
        self.comb += [
            crossbar.master.source.connect(tx.sink),
            rx.source.connect(crossbar.master.sink)
        ]
