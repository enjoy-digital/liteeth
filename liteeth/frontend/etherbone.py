from liteeth.common import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_packet import *

# etherbone packet

class LiteEthEtherbonePacketPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_etherbone_packet_description(32),
            eth_udp_user_description(32),
            etherbone_packet_header)


class LiteEthEtherbonePacketTX(Module):
    def __init__(self, udp_port):
        self.sink = sink = Sink(eth_etherbone_packet_user_description(32))
        self.source = source = Source(eth_udp_user_description(32))

        # # #

        self.submodules.packetizer = packetizer = LiteEthEtherbonePacketPacketizer()
        self.comb += [
            packetizer.sink.stb.eq(sink.stb),
            packetizer.sink.sop.eq(sink.sop),
            packetizer.sink.eop.eq(sink.eop),
            sink.ack.eq(packetizer.sink.ack),

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
            packetizer.source.ack.eq(1),
            If(packetizer.source.stb & packetizer.source.sop,
                packetizer.source.ack.eq(0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            Record.connect(packetizer.source, source),
            source.src_port.eq(udp_port),
            source.dst_port.eq(udp_port),
            source.ip_address.eq(sink.ip_address),
            source.length.eq(sink.length + etherbone_packet_header.length),
            If(source.stb & source.eop & source.ack,
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
        self.sink = sink = Sink(eth_udp_user_description(32))
        self.source = source = Source(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.depacketizer = depacketizer = LiteEthEtherbonePacketDepacketizer()
        self.comb += Record.connect(sink, depacketizer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            depacketizer.source.ack.eq(1),
            If(depacketizer.source.stb & depacketizer.source.sop,
                depacketizer.source.ack.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal()
        self.sync += valid.eq(
            depacketizer.source.stb &
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
            source.sop.eq(depacketizer.source.sop),
            source.eop.eq(depacketizer.source.eop),

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


class LiteEthEtherbonePacket(Module):
    def __init__(self, udp, udp_port):
        self.submodules.tx = tx = LiteEthEtherbonePacketTX(udp_port)
        self.submodules.rx = rx = LiteEthEtherbonePacketRX()
        udp_port = udp.crossbar.get_port(udp_port, dw=32)
        self.comb += [
            Record.connect(tx.source, udp_port.sink),
            Record.connect(udp_port.source, rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source


# etherbone probe

class LiteEthEtherboneProbe(Module):
    def __init__(self):
        self.sink = sink = Sink(eth_etherbone_packet_user_description(32))
        self.source = source = Source(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ack.eq(1),
            If(sink.stb & sink.sop,
                sink.ack.eq(0),
                NextState("PROBE_RESPONSE")
            )
        )
        fsm.act("PROBE_RESPONSE",
            Record.connect(sink, source),
            source.pf.eq(0),
            source.pr.eq(1),
            If(source.stb & source.eop & source.ack,
                NextState("IDLE")
            )
        )

# etherbone record

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
    def __init__(self, buffer_depth=256):
        self.sink = sink = Sink(eth_etherbone_record_description(32))
        self.source = source = Source(eth_etherbone_mmap_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        fifo = SyncFIFO(eth_etherbone_record_description(32), buffer_depth,
                        buffered=True)
        self.submodules += fifo
        self.comb += Record.connect(sink, fifo.sink)

        base_addr = Signal(32)
        base_addr_update = Signal()
        self.sync += If(base_addr_update, base_addr.eq(fifo.source.data))

        self.submodules.counter = counter = Counter(max=512)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            fifo.source.ack.eq(1),
            counter.reset.eq(1),
            If(fifo.source.stb & fifo.source.sop,
                base_addr_update.eq(1),
                If(fifo.source.wcount,
                    NextState("RECEIVE_WRITES")
                ).Elif(fifo.source.rcount,
                    NextState("RECEIVE_READS")
                )
            )
        )
        fsm.act("RECEIVE_WRITES",
            source.stb.eq(fifo.source.stb),
            source.sop.eq(counter.value == 0),
            source.eop.eq(counter.value == fifo.source.wcount-1),
            source.count.eq(fifo.source.wcount),
            source.be.eq(fifo.source.byte_enable),
            source.addr.eq(base_addr[2:] + counter.value),
            source.we.eq(1),
            source.data.eq(fifo.source.data),
            fifo.source.ack.eq(source.ack),
            If(source.stb & source.ack,
                counter.ce.eq(1),
                If(source.eop,
                    If(fifo.source.rcount,
                        NextState("RECEIVE_BASE_RET_ADDR")
                    ).Else(
                        NextState("IDLE")
                    )
                )
            )
        )
        fsm.act("RECEIVE_BASE_RET_ADDR",
            counter.reset.eq(1),
            If(fifo.source.stb & fifo.source.sop,
                base_addr_update.eq(1),
                NextState("RECEIVE_READS")
            )
        )
        fsm.act("RECEIVE_READS",
            source.stb.eq(fifo.source.stb),
            source.sop.eq(counter.value == 0),
            source.eop.eq(counter.value == fifo.source.rcount-1),
            source.count.eq(fifo.source.rcount),
            source.base_addr.eq(base_addr),
            source.addr.eq(fifo.source.data[2:]),
            fifo.source.ack.eq(source.ack),
            If(source.stb & source.ack,
                counter.ce.eq(1),
                If(source.eop,
                    NextState("IDLE")
                )
            )
        )


class LiteEthEtherboneRecordSender(Module):
    def __init__(self, buffer_depth=256):
        self.sink = sink = Sink(eth_etherbone_mmap_description(32))
        self.source = source = Source(eth_etherbone_record_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        pbuffer = Buffer(eth_etherbone_mmap_description(32), buffer_depth)
        self.submodules += pbuffer
        self.comb += Record.connect(sink, pbuffer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            pbuffer.source.ack.eq(1),
            If(pbuffer.source.stb & pbuffer.source.sop,
                pbuffer.source.ack.eq(0),
                NextState("SEND_BASE_ADDRESS")
            )
        )
        self.comb += [
            source.byte_enable.eq(pbuffer.source.be),
            If(pbuffer.source.we,
                source.wcount.eq(pbuffer.source.count)
            ).Else(
                source.rcount.eq(pbuffer.source.count)
            )
        ]

        fsm.act("SEND_BASE_ADDRESS",
            source.stb.eq(pbuffer.source.stb),
            source.sop.eq(1),
            source.eop.eq(0),
            source.data.eq(pbuffer.source.base_addr),
            If(source.ack,
                NextState("SEND_DATA")
            )
        )
        fsm.act("SEND_DATA",
            source.stb.eq(pbuffer.source.stb),
            source.sop.eq(0),
            source.eop.eq(pbuffer.source.eop),
            source.data.eq(pbuffer.source.data),
            If(source.stb & source.ack,
                pbuffer.source.ack.eq(1),
                If(source.eop,
                    NextState("IDLE")
                )
            )
        )


class LiteEthEtherboneRecord(Module):
    # Limitation: For simplicity we only support 1 record per packet
    def __init__(self, endianness="big"):
        self.sink = sink = Sink(eth_etherbone_packet_user_description(32))
        self.source = source = Sink(eth_etherbone_packet_user_description(32))

        # # #

        # receive record, decode it and generate mmap stream
        self.submodules.depacketizer = depacketizer = LiteEthEtherboneRecordDepacketizer()
        self.submodules.receiver = receiver = LiteEthEtherboneRecordReceiver()
        self.comb += [
            Record.connect(sink, depacketizer.sink),
            Record.connect(depacketizer.source, receiver.sink)
        ]
        if endianness is "big":
            self.comb += receiver.sink.data.eq(reverse_bytes(depacketizer.source.data))

        # save last ip address
        last_ip_address = Signal(32)
        self.sync += [
            If(sink.stb & sink.sop & sink.ack,
                last_ip_address.eq(sink.ip_address)
            )
        ]

        # receive mmap stream, encode it and send records
        self.submodules.sender = sender = LiteEthEtherboneRecordSender()
        self.submodules.packetizer = packetizer = LiteEthEtherboneRecordPacketizer()
        self.comb += [
            Record.connect(sender.source, packetizer.sink),
            Record.connect(packetizer.source, source),
            # XXX improve this
            source.length.eq(sender.source.wcount*4 + 4 + etherbone_record_header.length),
            source.ip_address.eq(last_ip_address)
        ]
        if endianness is "big":
            self.comb += packetizer.sink.data.eq(reverse_bytes(sender.source.data))



# etherbone wishbone

class LiteEthEtherboneWishboneMaster(Module):
    def __init__(self):
        self.sink = sink = Sink(eth_etherbone_mmap_description(32))
        self.source = source = Source(eth_etherbone_mmap_description(32))
        self.bus = bus = wishbone.Interface()

        # # #

        data = Signal(32)
        data_update = Signal()
        self.sync += If(data_update, data.eq(bus.dat_r))

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ack.eq(1),
            If(sink.stb & sink.sop,
                sink.ack.eq(0),
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
            bus.stb.eq(sink.stb),
            bus.we.eq(1),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                sink.ack.eq(1),
                If(sink.eop,
                    NextState("IDLE")
                )
            )
        )
        fsm.act("READ_DATA",
            bus.adr.eq(sink.addr),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.stb),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                data_update.eq(1),
                NextState("SEND_DATA")
            )
        )
        fsm.act("SEND_DATA",
            source.stb.eq(sink.stb),
            source.sop.eq(sink.sop),
            source.eop.eq(sink.eop),
            source.base_addr.eq(sink.base_addr),
            source.addr.eq(sink.addr),
            source.count.eq(sink.count),
            source.be.eq(sink.be),
            source.we.eq(1),
            source.data.eq(data),
            If(source.stb & source.ack,
                sink.ack.eq(1),
                If(source.eop,
                    NextState("IDLE")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )


# etherbone

class LiteEthEtherbone(Module):
    def __init__(self, udp, udp_port):
        # decode/encode etherbone packets
        self.submodules.packet = packet = LiteEthEtherbonePacket(udp, udp_port)

        # packets can be probe (etherbone discovering) or records with
        # writes and reads
        self.submodules.probe = probe = LiteEthEtherboneProbe()
        self.submodules.record = record = LiteEthEtherboneRecord()

        # arbitrate/dispatch probe/records packets
        dispatcher = Dispatcher(packet.source, [probe.sink, record.sink])
        self.comb += dispatcher.sel.eq(~packet.source.pf)
        arbiter = Arbiter([probe.source, record.source], packet.sink)
        self.submodules += dispatcher, arbiter

        # create mmap ŵishbone master
        self.submodules.master = master = LiteEthEtherboneWishboneMaster()
        self.comb += [
            Record.connect(record.receiver.source, master.sink),
            Record.connect(master.source, record.sender.sink)
        ]
