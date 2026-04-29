import unittest

from migen import *
from litex.gen import *

from litex.soc.interconnect.stream import Endpoint, EndpointDescription

from liteeth.common import *

from liteeth.core.rocev2.rocev2 import LiteEthIBTransportRX, LiteEthIBTransportTX, LiteEthIBQP, LiteEthIBSpecialQP, LiteEthIBTransportPacketizer
from liteeth.core.rocev2.mad_cm import LiteEthIBMAD, LiteEthCMPacketizer
from liteeth.core.udp import LiteEthUDPPacketizer, LiteEthUDPDepacketizer
from liteeth.core.ip import LiteEthIPV4Packetizer, LiteEthIPV4Depacketizer
from liteeth.core.rocev2.icrc import LiteEthInfinibandICRCInserter
import liteeth.core.rocev2.mr as mr

from collections import namedtuple
dummy_udp_port = namedtuple("udp_port", "address_width data_width")
dummy_udp_port.address_width = 24
dummy_udp_port.data_width = 8

class IPModule_dummy(LiteXModule):
    class IPTX(LiteXModule):
        def __init__(self, dw):
            self.sink = sink = Endpoint(eth_ipv4_user_description(dw))
            self.source = source = Endpoint(eth_mac_description(dw))

            self.packetizer = packetizer = LiteEthIPV4Packetizer(dw=8)

            self.comb += [
                packetizer.sink.target_ip.eq(sink.ip_address),
                packetizer.sink.total_length.eq(ipv4_header.length + sink.length),
                packetizer.sink.version.eq(0x4),     # ipv4
                packetizer.sink.ihl.eq(ipv4_header.length//4),
                # RDMA
                packetizer.sink.dont_fragment.eq(1),
                packetizer.sink.identification.eq(0),
                packetizer.sink.ttl.eq(0x80),
                packetizer.sink.sender_ip.eq(convert_ip("192.1.168.50"))
            ]

            self.comb += [
                sink.connect(packetizer.sink, keep={"valid", "ready", "last", "data"}),
                packetizer.source.connect(source)
            ]

    class IPRX(LiteXModule):
        def __init__(self, dw):
            self.sink = sink = Endpoint(eth_mac_description(dw))
            self.source = source = Endpoint(eth_ipv4_user_description(dw))

            self.depacketizer = depacketizer = LiteEthIPV4Depacketizer(dw=8)

            self.comb += [
                depacketizer.source.connect(source, keep={
                    "protocol",
                    "error",
                    "last_be"}),
                source.length.eq(depacketizer.source.total_length - ipv4_header_length),
                source.ip_address.eq(depacketizer.source.sender_ip),
            ]

            self.comb += [
                sink.connect(depacketizer.sink),
                depacketizer.source.connect(source, keep={"valid", "ready", "last", "data"})
            ]

    def __init__(self, dw):
        self.tx = self.IPTX(dw)
        self.rx = self.IPRX(dw)

class UDPModule_dummy(LiteXModule):
    class UDPTX(LiteXModule):
        def __init__(self, dw):
            self.sink = sink = Endpoint(eth_udp_user_description(dw))
            self.source = source = Endpoint(eth_ipv4_user_description(dw))

            self.packetizer = packetizer = LiteEthUDPPacketizer(dw=8)

            self.comb += [
            sink.connect(packetizer.sink, keep={
                "last_be",
                "src_port",
                "dst_port"}),
            packetizer.sink.length.eq(sink.length + udp_header.length),
            packetizer.sink.checksum.eq(0), # UDP Checksum is not used, we only rely on MAC CRC.
        ]

            self.comb += [
                sink.connect(packetizer.sink, keep={"valid", "ready", "last", "data"}),
                packetizer.source.connect(source),
            ]

    class UDPRX(LiteXModule):
        def __init__(self, dw):
            self.sink = sink = Endpoint(eth_ipv4_user_description(dw))
            self.source = source = Endpoint(eth_udp_user_description(dw))

            self.depacketizer = depacketizer = LiteEthUDPDepacketizer(dw=8)

            self.comb += [
                sink.connect(depacketizer.sink),
                depacketizer.source.connect(source, keep={
                    "src_port",
                    "dst_port",
                    "error"}),
                source.ip_address.eq(sink.ip_address),
                source.length.eq(depacketizer.source.length - udp_header.length),
            ]

            self.comb += [
                sink.connect(depacketizer.sink),
                depacketizer.source.connect(source, keep={"valid", "ready", "last", "data"})
            ]

    def __init__(self, ip, dw):
        self.tx = tx = self.UDPTX(dw)
        self.rx = rx = self.UDPRX(dw)

        self.comb += [
            tx.source.connect(ip.tx.sink),
            ip.rx.source.connect(rx.sink)
        ]

class DUT(LiteXModule):
    qp_psn = 0
    sp_qp_psn = 0

    def __init__(self):
        # Packetizer will feed into the rocev2 module as if packets were being received through udp
        self.mad_pack = mad_pack = LiteEthCMPacketizer(dw=8)
        self.ib_pack = ib_pack = LiteEthIBTransportPacketizer(dw=8)

        self.udp_pack = udp_pack = UDPModule_dummy.UDPTX(dw=8)
        self.ip_pack = ip_pack = IPModule_dummy.IPTX(dw=8)
        #self.ip_buff = ip_buff = stream.Buffer(eth_ipv4_user_description(dw=8))

        self.direct_ib_sink = direct_ib_sink = Endpoint(eth_rocev2_description(dw=8))
        self.direct_bytestream_sink = direct_bytestream_sink = Endpoint(eth_mac_description(dw=8))
        self.direct_bytestream = Signal()
        self.mad_packet = Signal()

        # The memory region is replaced with a dummy

        self.mrs = mr.LiteEthIBMemoryRegions()

        # self.mem = Memory(16, 1024)
        # self.remote_wport = self.mem.get_port(write_capable=True)
        # self.remote_rport = self.mem.get_port()
        # self.mrs.reg_mr(
        #     region_start = 0,
        #     region_size  = 2048,
        #     permissions  = mr.PERM.REMOTE_WRITE | mr.PERM.REMOTE_READ,
        #     r_key        = 0xdeadbeef,
        #     read_port    = self.remote_rport,
        #     write_port   = self.remote_wport
        # )

        self.wmem = Memory(16, 2**10)
        self.remote_wport = self.wmem.get_port(write_capable=True)
        self.mrs.reg_mr(
            region_start = 0,
            region_size  = 2**11,
            permissions  = mr.PERM.REMOTE_WRITE,
            r_key        = 0x11111111,
            read_port    = None,
            write_port   = self.remote_wport
        )

        self.rmem = Memory(16, 2**10)
        self.remote_rport = self.rmem.get_port()
        self.mrs.reg_mr(
            region_start = 0,
            region_size  = 2**11,
            permissions  = mr.PERM.REMOTE_READ,
            r_key        = 0x22222222,
            read_port    = self.remote_rport,
            write_port   = None
        )

        self.rwmem = Memory(16, 2**10)
        self.remote_rw_wport = self.rwmem.get_port(write_capable=True)
        self.remote_rw_rport = self.rwmem.get_port()
        self.mrs.reg_mr(
            region_start = 0,
            region_size  = 2**11,
            permissions  = mr.PERM.REMOTE_READ | mr.PERM.REMOTE_WRITE,
            r_key        = 0xdeadbeef,
            read_port    = self.remote_rw_rport,
            write_port   = self.remote_rw_wport
        )

        qp = LiteEthIBQP(id=0xdeaded)
        self.submodules += qp

        special_qp = LiteEthIBSpecialQP()
        self.submodules += special_qp

        self.qps = qps = [special_qp, qp]

        self.mad = mad = LiteEthIBMAD(qps, 10e6, dw=8)

        self.ip_dummy = ip_dummy = IPModule_dummy(dw=8)
        self.udp_dummy = udp_dummy = UDPModule_dummy(ip_dummy, dw=8)

        self.rocev2_tx = rocev2_tx = LiteEthIBTransportTX(ip_dummy, mad.tx, qps, self.mrs.get_mrs(), with_crc=True, buffered_out=True)
        self.rocev2_rx = rocev2_rx = LiteEthIBTransportRX(ip_dummy, rocev2_tx.ack_sink, rocev2_tx.read_requests_sink, rocev2_tx.resp_choose_sink, mad.rx, qps, self.mrs.get_mrs(), with_crc=True)

        self.crc_ins = crc_ins = LiteEthInfinibandICRCInserter(eth_mac_description(dw=8), eth_rocev2_description(dw=8))

        self.length = Signal(16)

        # send_direct_bytestream:
        # direct_bytestream_sink --> crc_ins --> ip_dummy_rx --> udp_dummy.rx --> rocev2_rx
        #                               ^-----/                                       |
        #                                                                             v
        #                                        ip_dummy_tx <-- udp_dummy.tx <-- rocev2_tx
        #####################################################################################################
        # send_mad_packet:
        # mad_pack --> ib_pack --> udp_pack --> ip_pack --> ip_dummy_rx --> udp_dummy.rx --> rocev2_rx
        #                                                                                        |
        #                                                                                        v
        #                                                   ip_dummy_tx <-- udp_dummy.tx <-- rocev2_tx
        #####################################################################################################
        # send_ib_packet:
        # direct_ib_sink --> ib_pack --> udp_pack --> ip_pack --> ip_dummy_rx --> udp_dummy.rx --> rocev2_rx
        #                                                                                              |
        #                                                                                              v
        #                                                         ip_dummy_tx <-- udp_dummy.tx <-- rocev2_tx

        self.comb += [
            rocev2_tx.enable.eq(1),
            If(self.direct_bytestream,
                direct_bytestream_sink.connect(crc_ins.sink, keep={"valid", "ready", "last", "data"}),
                #crc_ins.source.connect(ip_buff.sink, keep={"valid", "ready", "last", "data"}),
                #ip_buff.source.connect(ip_dummy.rx.sink, keep={"valid", "ready", "last", "data"}),
                crc_ins.source.connect(ip_dummy.rx.sink, keep={"valid", "ready", "last", "data"}),
                ip_dummy.rx.sink.connect(crc_ins.calculator_sink, keep={"valid", "data", "last"}),
                # We listen passively to the output of ip, so no control of ready (we don't use connect for it)
                crc_ins.calculator_sink.ready.eq(ip_dummy.rx.sink.ready)
            ).Else(
                If(self.mad_packet,
                    mad_pack.source.connect(ib_pack.sink, keep={"valid", "ready", "last", "data"})
                ).Else(
                    direct_ib_sink.connect(ib_pack.sink, keep={"valid", "ready", "last", "data"})
                ),
                ib_pack.source.connect(udp_pack.sink),
                udp_pack.source.connect(ip_pack.sink),
                ip_pack.source.connect(ip_dummy.rx.sink),
            ),

            udp_dummy.rx.source.connect(rocev2_rx.sink),
            rocev2_tx.source.connect(udp_dummy.tx.sink),

            rocev2_rx.source.ready.eq(1),
            ip_dummy.tx.source.ready.eq(1)
        ]

    def set_mad_packet_params(self, attrib):
        # MAD
        yield self.mad_pack.sink.BaseVersion.eq(0x01)
        yield self.mad_pack.sink.MgmtClass.eq(0x07)
        yield self.mad_pack.sink.ClassVersion.eq(0x02)
        yield self.mad_pack.sink.Method.eq(0x03)
        yield self.mad_pack.sink.AttributeID.eq(attrib)

        # CM
        yield self.mad_pack.sink.Local_QPN.eq(0x11)
        yield self.mad_pack.sink.Responder_Resources.eq(0x01)
        yield self.mad_pack.sink.Initiator_Depth.eq(0x01)
        yield self.mad_pack.sink.Partition_Key.eq(0xffff)
        yield self.mad_pack.sink.Starting_PSN.eq(0xcafeed)
        yield self.mad_pack.sink.Primary_Local_Port_GID.eq(Cat(convert_ip("192.168.1.1"), Constant(0xffff, 16)))

        self.qp_psn = 0xcafeed

    def set_ib_packet_params(self, opcode, psn, dest_qp=0xdeaded, length=PMTU):
        yield self.length.eq(length)
        # Set opcode
        yield self.ib_pack.sink.opcode.eq(opcode)
        yield self.ib_pack.sink.psn.eq(psn)
        yield self.ib_pack.sink.tver.eq(0)
        yield self.ib_pack.sink.dest_qp.eq(dest_qp)
        yield self.ib_pack.sink.pad.eq((4 - (length % 4)) % 4)

        yield self.ib_pack.sink.se.eq(0)
        yield self.ib_pack.sink.m.eq(0)
        yield self.ib_pack.sink.a.eq(1)
        yield self.ib_pack.sink.se.eq(0)
        yield self.ib_pack.sink.p_key.eq(DEFAULT_P_KEY)

        if dest_qp == 1:
            yield self.ib_pack.sink.q_key.eq(DEFAULT_CM_Q_Key)
            yield self.ib_pack.sink.src_qp.eq(1)

        # # Randomize parameters
        # for p in self.pack.sink.description.param_layout:
        #     if p[0] != "opcode" and p[0] != "r_key" and p[0] != "psn" and p[0] != "tver"and p[0] != "dest_qp" and p[0] != "pad":
        #         yield from random_signal(getattr(self.pack.sink, p[0]), signal_name=p[0])
        psn += 1

    def set_rdma_params(self):
        virtual_address = 0
        dma_len = 10
        yield self.ib_pack.sink.va.eq(virtual_address)
        yield self.ib_pack.sink.r_key.eq(0xdeadbeef)
        yield self.ib_pack.sink.dma_len.eq(dma_len)

    # Configures Infiniband transport layer packetizer
    def setup_ib(self, opcode, psn, dest_qp=0xdeaded, length=PMTU):
        header_only = (length == 0)
        yield from self.set_ib_packet_params(opcode, psn, dest_qp, length)

        opcode_conn_type = (opcode & 0b11100000) >> 5
        opcode_op        = opcode & 0b00011111
        if opcode_op in [
            BTH_OPCODE_OP.RDMA_WRITE_First,
            BTH_OPCODE_OP.RDMA_WRITE_Middle,
            BTH_OPCODE_OP.RDMA_WRITE_Last,
            BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate,
            BTH_OPCODE_OP.RDMA_WRITE_Only,
            BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
        ]:
            yield from self.set_rdma_params()

        yield self.ib_pack.header_only.eq(header_only)

    # Sends an Infiniband transport layer packet
    def send_ib_packet(self, opcode, dest_qp=0xdeaded, length=PMTU):
        header_only = (length == 0)
        yield self.mad_packet.eq(0)
        yield from self.setup_ib(opcode, self.qp_psn, dest_qp=dest_qp, length=length)
        self.qp_psn += 1

        yield self.direct_ib_sink.valid.eq(1)
        i = 0
        if not header_only:
            yield self.direct_ib_sink.data.eq(i)
        yield

        while (yield self.direct_ib_sink.ready) == 0:
            yield

        if not header_only:
            while i < length - 1:
                if (yield self.direct_ib_sink.ready) == 1:
                    i += 1
                    yield self.direct_ib_sink.data.eq(i)
                yield

        yield self.direct_ib_sink.data.eq(0xee)
        j = 0
        while j < 4:
            if (yield self.direct_ib_sink.ready):
                j += 1
                if j == 4:
                    yield self.direct_ib_sink.last.eq(1)
            yield

        yield self.direct_ib_sink.last.eq(0)
        yield self.direct_ib_sink.valid.eq(0)
        yield

    # Sends a MAD Communication Management packet
    def send_mad_packet(self, attrib):
        length = 256
        yield self.mad_packet.eq(1)

        # Setup MAD and Transport layer packetizers
        yield from self.set_mad_packet_params(attrib)
        yield from self.setup_ib(BTH_OPCODE.UD.SEND_Only, self.sp_qp_psn, dest_qp=1, length=length)
        self.sp_qp_psn += 1
        yield self.mad_pack.header_only.eq(0)

        # Turn on packetizer
        i = 0
        yield self.mad_pack.sink.data.eq(i)
        yield self.mad_pack.sink.valid.eq(1)
        yield

        # Wait for packetizer to start accepting data
        while (yield self.mad_pack.sink.ready) == 0:
            if (yield self.mad_pack.source.ready) and (yield self.mad_pack.source.valid):
                i += 1
            yield

        # Send data
        while i < length - 1:
            if (yield self.mad_pack.sink.ready):
                i += 1
                yield self.mad_pack.sink.data.eq(i)
            yield

        yield self.mad_pack.sink.data.eq(0xee)
        j = 0
        while j < 4:
            if (yield self.mad_pack.sink.ready):
                j += 1
                if j == 4:
                    yield self.mad_pack.sink.last.eq(1)
            yield

        # Turn off packetizer
        yield self.mad_pack.sink.last.eq(0)
        yield self.mad_pack.sink.valid.eq(0)
        yield self.mad_packet.eq(0)
        yield

    def send_direct_stream(self, bytestring):
        yield self.direct_bytestream_sink.valid.eq(1)
        for i, byte in enumerate(bytestring):
            yield self.direct_bytestream_sink.data.eq(byte)
            if i == len(bytestring) - 1:
                yield self.direct_bytestream_sink.last.eq(1)
            yield
            while not (yield self.direct_bytestream_sink.ready):
                yield
        yield self.direct_bytestream_sink.last.eq(0)
        yield self.direct_bytestream_sink.valid.eq(0)
        yield

# @passive
# def memory_region_dummy_act(dut):
#     bank_size = 4096
#     address_pipe = [(0, 0)] * 256
#     pipe_read = 0
#     pipe_write = 0
#     level = 0
#     bank = [0] * bank_size
#     mem_region = list(dut.mrs.get_mrs())[0]

#     yield mem_region.reader.inner_reader_sink.ready.eq(1)
#     yield mem_region.writer.inner_writer_sink.ready.eq(1)

#     delay = 0
#     burst = 10
#     while True:
#         # Read
#         if (yield mem_region.reader.inner_reader_sink.valid):
#             address_pipe[pipe_read] = ((yield mem_region.reader.inner_reader_sink.address), (yield mem_region.reader.inner_reader_sink.last))
#             assert address_pipe[pipe_read][0] >= 0 and address_pipe[pipe_read][0] < bank_size
#             pipe_read = (pipe_read + 1) % 256
#             level += 1

#         if level != 0:
#             source_ready = (yield mem_region.reader.inner_reader_source.ready)
#             source_valid = (yield mem_region.reader.inner_reader_source.valid)
#             source_last = (yield mem_region.reader.inner_reader_source.last)

#             if source_last:
#                 if source_ready and source_valid:
#                     pipe_write = (pipe_write + 1) % 256
#                     level -= 1
#                     yield mem_region.reader.inner_reader_source.valid.eq(0)
#                     yield mem_region.reader.inner_reader_source.last.eq(0)
#             else:
#                 if source_ready and source_valid:
#                     pipe_write = (pipe_write + 1) % 256
#                     level -= 1
#                     yield mem_region.reader.inner_reader_source.valid.eq(1)
#                     yield mem_region.reader.inner_reader_source.data.eq(bank[address_pipe[pipe_write][0]])
#                     yield mem_region.reader.inner_reader_source.last.eq(address_pipe[pipe_write][1])
#                 elif source_ready:
#                     yield mem_region.reader.inner_reader_source.valid.eq(1)
#                     yield mem_region.reader.inner_reader_source.data.eq(bank[address_pipe[pipe_write][0]])
#                     yield mem_region.reader.inner_reader_source.last.eq(address_pipe[pipe_write][1])
#                 else:
#                     yield mem_region.reader.inner_reader_source.valid.eq(0)
#                     yield mem_region.reader.inner_reader_source.data.eq(0)
#                     yield mem_region.reader.inner_reader_source.last.eq(0)

#         # Write
#         if delay == 0:
#             ready = (yield mem_region.writer.inner_writer_sink.ready)
#             if (yield mem_region.writer.inner_writer_sink.valid) and ready:
#                 addr = (yield mem_region.writer.inner_writer_sink.address)
#                 assert addr >= 0 and addr < bank_size

#                 bank[addr] = (yield mem_region.writer.inner_writer_sink.data)

#                 burst -= 1
#                 if burst == 0:
#                     burst = 16
#                     delay = 10
#                     yield mem_region.writer.inner_writer_sink.ready.eq(0)
#             if not ready:
#                 yield mem_region.writer.inner_writer_sink.ready.eq(1)
#         else:
#             delay -= 1
#         yield

def add_byte(out, n):
    if n < 0 or n > 255:
        raise ValueError
    out += f"\\x{n:02x}"

# Do side operations on every clock front
@passive
def record_msg(dut):
    out = ""
    while True:
        if ((yield dut.ib_pack.source.ready) and (yield dut.ib_pack.source.valid)):
            add_byte(out, (yield dut.ib_pack.source.data))
            if (yield dut.ib_pack.source.last):
                print(out + "\n")
                out = ""
        yield

from random import randint
choices = {}
def random_signal(signal, signal_name=""):
    global choices
    l = len(signal)
    r = randint(0, (1 << l) - 1)
    choices[signal_name] = r
    yield signal.eq(r)

    # yield from mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    # yield from mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    # print(out + "\n"); out = ""
    # yield from send_ib_packet(BTH_OPCODE.RC.RDMA_WRITE_Only, length=10)
    # print(out + "\n"); out = ""
    # yield from send_ib_packet(BTH_OPCODE.RC.RDMA_READ_Request, length=0)
    # # yield from send_ib_packet(BTH_OPCODE.UD.SEND_Only, dest_qp=1)
    # print(out + "\n"); out = ""
    # yield from mad_packet(MAD_ATTRIB_ID.ReadyToUse)
    # print(out + "\n"); out = ""
    # # yield from packet(0b00110)

    # # yield from packet(0b00100, length=4)
    # for i in range(300):
    #     yield

    # yield from send_ib_packet(BTH_OPCODE.RC.RDMA_WRITE_Only, length=256)
    # print(out + "\n"); out = ""
    # yield from send_ib_packet(BTH_OPCODE.RC.RDMA_READ_Request, length=0)
    # # yield from send_ib_packet(BTH_OPCODE.UD.SEND_Only, dest_qp=1)
    # print(out + "\n"); out = ""
    # yield from mad_packet(MAD_ATTRIB_ID.DisconnectRequest)

def exchange_send(dut):
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    print(1)
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.ReadyToUse)
    print(2)
    yield from dut.send_ib_packet(BTH_OPCODE_OP.SEND_Only)
    print(3)
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.DisconnectRequest)

def exchange_direct(dut):
    packets = [
        "45000134d81f40004011de15c0a80101c0a80132d87212b7012000006400ffff000000018000004280010000000000010107020300000000000000238645ae35001000000000000035ae4586000000000000000001061c06aaa159fffe8f304c00000000000000000000280100000001000000a01e5a52a7ffff17f0ffffffff00000000000000000000ffffc0a8010100000000000000000000ffffc0a801325d3510000040008800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040b355000000000000000000000000c0a80101000000000000000000000000c0a801320000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "45000134df1d40004011d717c0a80101c0a80132d87212b7012000006400ffff000000018000004380010000000000010107020300000000000000238645ae35001400000000000035ae4586fedcabed0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "4500013cdfe740004011d645c0a80101c0a80132d34612b7012800000600ffff00deaded00fedfed0000000000000100deadbeef000004006161616161616161616161616161616161616161626262626262626262626262626262626262626263636363636363636363636363636363636363636464646464646464646464646464646464646464656565656565656565656565656565656565656566666666666666666666666666666666666666666767676767676767676767676767676767676767686868686868686868686868686868686868686869696969696969696969696969696969696969696a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6a6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6d",
        "4500012cdfe840004011d654c0a80101c0a80132d34612b7011800000700ffff00deaded00fedfee6d6d6d6d6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6e6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f6f70707070707070707070707070707070707070707171717171717171717171717171717171717171727272727272727272727272727272727272727273737373737373737373737373737373737373737474747474747474747474747474747474747474757575757575757575757575757575757575757576767676767676767676767676767676767676767777777777777777777777777777777777777777787878787878787878787878787878787878787879797979797979797979797979797979797979797a7a7a7a7a7a7a7a7a7a7a7a",
        "4500012cdfe940004011d653c0a80101c0a80132d34612b7011800000700ffff00deaded00fedfef7a7a7a7a7a7a7a7a7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f80808080808080808080808080808080808080808181818181818181818181818181818181818181828282828282828282828282828282828282828283838383838383838383838383838383838383838484848484848484848484848484848484848484858585858585858585858585858585858585858586868686868686868686868686868686868686868787878787878787",
        "4500012cdfea40004011d652c0a80101c0a80132d34612b7011800000800ffff00deaded80fedff0878787878787878787878787888888888888888888888888888888888888888889898989898989898989898989898989898989898a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f909090909090909090909090909090909090909091919191919191919191919191919191919191919292929292929292929292929292929292929292414141414141414141414141414141414141414141414100",
        "4500003ce98c40004011cda0c0a80101c0a80132d34612b7002800000c00ffff00deaded80fedff10000000000000000deadbeef00000400",
        "wait",
        "45000134f0d840004011c55cc0a80101c0a80132d87212b7012000006400ffff000000018000004480010000000000010107020300000000000000238645ae35001500000000000035ae4586fedcabeddeaded0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    ]

    for _ in range(1):
        yield dut.direct_bytestream.eq(1)
        packetn = 0
        for packetdata in packets:
            if packetdata[0] == "w":
                t = int(packetdata[1:])
                for _ in range(t):
                    yield
                print(f"Waited for {t} cycles")
            else:
                yield from dut.send_direct_stream(bytes.fromhex(packetdata))
                print(f"Sent packet number {packetn}")
                packetn += 1

        print("Waiting for completion")
        for _ in range(2000):
            yield
        print("Done!")

        yield dut.direct_bytestream.eq(0)

class TestRoCEv2(unittest.TestCase):
    def test_exchange(self):
        dut = DUT()

        run_simulation(dut, [exchange_send(dut), record_msg(dut)], vcd_name="test.vcd")

    def test_direct(self):
        dut = DUT()

        run_simulation(dut, [exchange_direct(dut)], vcd_name="test.vcd")

from migen.fhdl.verilog import convert
convert(DUT()).write("my_design.v")
