#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""
Etherbone

CERN's Etherbone protocol is initially used to run a Wishbone bus over an
ethernet network. This re-implementation is meant to be run over ethernet
and introduces some limitations:
- no address spaces (rca/bca/wca/wff)
- 32bits data and address
- 1 record per frame
"""

from liteeth.common import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.packet import *

# Etherbone Packet ---------------------------------------------------------------------------------

class LiteEthEtherbonePacketPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_etherbone_packet_description(32),
            eth_udp_user_description(32),
            etherbone_packet_header)


class LiteEthEtherbonePacketTX(Module):
    def __init__(self, udp_port):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(eth_udp_user_description(32))

        # # #

        self.submodules.packetizer = packetizer = LiteEthEtherbonePacketPacketizer()
        self.comb += [
            packetizer.sink.valid.eq(sink.valid),
            packetizer.sink.last.eq(sink.last),
            sink.ready.eq(packetizer.sink.ready),

            packetizer.sink.magic.eq(etherbone_magic),
            packetizer.sink.port_size.eq(32//8),
            packetizer.sink.addr_size.eq(32//8),
            packetizer.sink.pf.eq(sink.pf),
            packetizer.sink.pr.eq(sink.pr),
            packetizer.sink.nr.eq(sink.nr),
            packetizer.sink.version.eq(etherbone_version),

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
            source.src_port.eq(udp_port),
            source.dst_port.eq(udp_port),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length + etherbone_packet_header.length),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )


class LiteEthEtherbonePacketDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_udp_user_description(32),
            eth_etherbone_packet_description(32),
            etherbone_packet_header)


class LiteEthEtherbonePacketRX(Module):
    def __init__(self):
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.depacketizer = depacketizer = LiteEthEtherbonePacketDepacketizer()
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
            (depacketizer.source.magic == etherbone_magic)
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

            source.pf.eq(depacketizer.source.pf),
            source.pr.eq(depacketizer.source.pr),
            source.nr.eq(depacketizer.source.nr),

            source.data.eq(depacketizer.source.data),

            source.src_port.eq(sink.src_port),
            source.dst_port.eq(sink.dst_port),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length - etherbone_packet_header.length)
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


class LiteEthEtherbonePacket(Module):
    def __init__(self, udp, udp_port, cd="sys"):
        self.submodules.tx = tx = LiteEthEtherbonePacketTX(udp_port)
        self.submodules.rx = rx = LiteEthEtherbonePacketRX()
        udp_port = udp.crossbar.get_port(udp_port, dw=32, cd=cd)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source


# Etherbone Probe ----------------------------------------------------------------------------------

class LiteEthEtherboneProbe(Module):
    def __init__(self):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                NextState("PROBE_RESPONSE")
            )
        )
        fsm.act("PROBE_RESPONSE",
            sink.connect(source),
            source.pf.eq(0),
            source.pr.eq(1),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )

# Etherbone Record ---------------------------------------------------------------------------------

class LiteEthEtherboneRecordPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_etherbone_record_description(32),
            eth_etherbone_packet_user_description(32),
            etherbone_record_header)


class LiteEthEtherboneRecordDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_etherbone_packet_user_description(32),
            eth_etherbone_record_description(32),
            etherbone_record_header)


class LiteEthEtherboneRecordReceiver(Module):
    def __init__(self, buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_record_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_mmap_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        fifo = stream.SyncFIFO(eth_etherbone_record_description(32), buffer_depth,
                               buffered=True)
        self.submodules += fifo
        self.comb += sink.connect(fifo.sink)

        base_addr = Signal(32, reset_less=True)
        base_addr_update = Signal()
        self.sync += If(base_addr_update, base_addr.eq(fifo.source.data))

        count = Signal(max=512, reset_less=True)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            fifo.source.ready.eq(1),
            NextValue(count, 0),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                If(fifo.source.wcount,
                    NextState("RECEIVE_WRITES")
                ).Elif(fifo.source.rcount,
                    NextState("RECEIVE_READS")
                )
            )
        )
        fsm.act("RECEIVE_WRITES",
            source.valid.eq(fifo.source.valid),
            source.last.eq(count == fifo.source.wcount-1),
            source.count.eq(fifo.source.wcount),
            source.be.eq(fifo.source.byte_enable),
            source.addr.eq(base_addr[2:] + count),
            source.we.eq(1),
            source.data.eq(fifo.source.data),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                NextValue(count, count + 1),
                If(source.last,
                    If(fifo.source.rcount,
                        NextState("RECEIVE_BASE_RET_ADDR")
                    ).Else(
                        NextState("IDLE")
                    )
                )
            )
        )
        fsm.act("RECEIVE_BASE_RET_ADDR",
            NextValue(count, 0),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                NextState("RECEIVE_READS")
            )
        )
        fsm.act("RECEIVE_READS",
            source.valid.eq(fifo.source.valid),
            source.last.eq(count == fifo.source.rcount-1),
            source.count.eq(fifo.source.rcount),
            source.base_addr.eq(base_addr),
            source.addr.eq(fifo.source.data[2:]),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                NextValue(count, count + 1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class LiteEthEtherboneRecordSender(Module):
    def __init__(self, buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_record_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        fifo = stream.SyncFIFO(eth_etherbone_mmap_description(32), buffer_depth, buffered=True)
        self.submodules += fifo
        self.comb += sink.connect(fifo.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            fifo.source.ready.eq(1),
            If(fifo.source.valid,
                fifo.source.ready.eq(0),
                NextState("SEND_BASE_ADDRESS")
            )
        )
        self.comb += [
            source.byte_enable.eq(fifo.source.be),
            If(fifo.source.we,
                source.wcount.eq(fifo.source.count)
            ).Else(
                source.rcount.eq(fifo.source.count)
            )
        ]
        fsm.act("SEND_BASE_ADDRESS",
            source.valid.eq(1),
            source.last.eq(0),
            source.data.eq(fifo.source.base_addr),
            If(source.ready,
                NextState("SEND_DATA")
            )
        )
        fsm.act("SEND_DATA",
            source.valid.eq(1),
            source.last.eq(fifo.source.last),
            source.data.eq(fifo.source.data),
            If(source.valid & source.ready,
                fifo.source.ready.eq(1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class LiteEthEtherboneRecord(Module):
    def __init__(self, endianness="big", buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        # Receive record, decode it and generate mmap stream
        self.submodules.depacketizer = depacketizer = LiteEthEtherboneRecordDepacketizer()
        self.submodules.receiver = receiver = LiteEthEtherboneRecordReceiver(buffer_depth)
        self.comb += [
            sink.connect(depacketizer.sink),
            depacketizer.source.connect(receiver.sink)
        ]
        if endianness is "big":
            self.comb += receiver.sink.data.eq(reverse_bytes(depacketizer.source.data))

        # Save last ip address
        first = Signal(reset=1)
        last_ip_address = Signal(32, reset_less=True)
        self.sync += [
            If(sink.valid & sink.ready,
                If(first,
                    last_ip_address.eq(sink.ip_address),
                ),
                first.eq(sink.last)
            )
        ]

        # Receive MMAP stream, encode it and send records
        self.submodules.sender     = sender     = LiteEthEtherboneRecordSender(buffer_depth)
        self.submodules.packetizer = packetizer = LiteEthEtherboneRecordPacketizer()
        self.comb += [
            sender.source.connect(packetizer.sink),
            packetizer.source.connect(source),
            source.length.eq(etherbone_record_header.length +
                             (sender.source.wcount != 0)*4 + sender.source.wcount*4 +
                             (sender.source.rcount != 0)*4 + sender.source.rcount*4),
            source.ip_address.eq(last_ip_address)
        ]
        if endianness is "big":
            self.comb += packetizer.sink.data.eq(reverse_bytes(sender.source.data))

# Etherbone Wishbone Master ------------------------------------------------------------------------

class LiteEthEtherboneWishboneMaster(Module):
    def __init__(self):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.bus    = bus    = wishbone.Interface()

        # # #

        data_update = Signal()

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                If(sink.we,
                    NextState("WRITE_DATA")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )
        fsm.act("WRITE_DATA",
            bus.adr.eq(sink.addr),
            bus.dat_w.eq(sink.data),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.we.eq(1),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                sink.ready.eq(1),
                If(sink.last,
                    NextState("IDLE")
                )
            )
        )
        fsm.act("READ_DATA",
            bus.adr.eq(sink.addr),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                data_update.eq(1),
                NextState("SEND_DATA")
            )
        )
        self.sync += [
            source.base_addr.eq(sink.base_addr),
            source.addr.eq(sink.addr),
            source.count.eq(sink.count),
            source.be.eq(sink.be),
            source.we.eq(1),
            If(data_update,
                source.data.eq(bus.dat_r)
            )
        ]
        fsm.act("SEND_DATA",
            source.valid.eq(sink.valid),
            source.last.eq(sink.last),
            If(source.valid & source.ready,
                sink.ready.eq(1),
                If(source.last,
                    NextState("IDLE")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )

# Etherbone Wishbone Slave -------------------------------------------------------------------------

class LiteEthEtherboneWishboneSlave(Module):
    def __init__(self):
        self.bus    = bus    = wishbone.Interface()
        self.sink   = sink   = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_mmap_description(32))

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(bus.stb & bus.cyc,
                If(bus.we,
                    NextState("SEND_WRITE")
                ).Else(
                    NextState("SEND_READ")
                )
            )
        )
        fsm.act("SEND_WRITE",
            source.valid.eq(1),
            source.last.eq(1),
            source.base_addr[2:].eq(bus.adr),
            source.count.eq(1),
            source.be.eq(bus.sel),
            source.we.eq(1),
            source.data.eq(bus.dat_w),
            If(source.valid & source.ready,
                bus.ack.eq(1),
                NextState("IDLE")
            )
        )
        fsm.act("SEND_READ",
            source.valid.eq(1),
            source.last.eq(1),
            source.base_addr.eq(0),
            source.count.eq(1),
            source.be.eq(bus.sel),
            source.we.eq(0),
            source.data[2:].eq(bus.adr),
            If(source.valid & source.ready,
                NextState("WAIT_READ")
            )
        )
        fsm.act("WAIT_READ",
            sink.ready.eq(1),
            If(sink.valid & sink.we,
                bus.ack.eq(1),
                bus.dat_r.eq(sink.data),
                NextState("IDLE")
            )
        )


# Etherbone ----------------------------------------------------------------------------------------

class LiteEthEtherbone(Module):
    def __init__(self, udp, udp_port, mode="master", buffer_depth=4, cd="sys"):
        # Encode/encode etherbone packets
        self.submodules.packet = packet = LiteEthEtherbonePacket(udp, udp_port, cd)

        # Packets can be probe (etherbone discovering) or records with writes and reads
        self.submodules.probe  = probe = LiteEthEtherboneProbe()
        self.submodules.record = record = LiteEthEtherboneRecord(buffer_depth=buffer_depth)

        # Arbitrate/dispatch probe/records packets
        dispatcher = Dispatcher(packet.source, [probe.sink, record.sink])
        self.comb += dispatcher.sel.eq(~packet.source.pf)
        arbiter = Arbiter([probe.source, record.source], packet.sink)
        self.submodules += dispatcher, arbiter

        # Create MMAP wishbone
        self.submodules.wishbone = {
            "master": LiteEthEtherboneWishboneMaster(),
            "slave":  LiteEthEtherboneWishboneSlave(),
        }[mode]
        self.comb += [
            record.receiver.source.connect(self.wishbone.sink),
            self.wishbone.source.connect(record.sender.sink)
        ]
