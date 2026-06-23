import unittest

from migen import *
from litex.gen import *

from litex.soc.interconnect.stream import Endpoint, EndpointDescription

from liteeth.common import *

from liteeth.core.rocev2.qp import LiteEthIBQP, LiteEthIBSpecialQP, LiteEthCQ
from liteeth.core.rocev2.rocev2 import LiteEthIBTransportRX, LiteEthIBTransportTX, LiteEthIBTransportPacketizer
from liteeth.core.rocev2.mad_cm import LiteEthIBMAD, LiteEthCMPacketizer
from liteeth.core.udp import LiteEthUDPPacketizer, LiteEthUDPDepacketizer
from liteeth.core.ip import LiteEthIPV4Packetizer, LiteEthIPV4Depacketizer
from liteeth.core.rocev2.icrc import LiteEthInfinibandICRCInserter
import liteeth.core.rocev2.mr as mr

from liteeth.core.rocev2.rdma_streamer import LiteEthRDMAStreamer

from collections import namedtuple
dummy_udp_port = namedtuple("udp_port", "address_width data_width")
dummy_udp_port.address_width = 24
dummy_udp_port.data_width = 8

class WR:
    def __init__(self, wr_opcode, l_key, va, dma_len, ack_req, immdt=None, r_key=None):
        if immdt is not None:
            assert wr_opcode in [WR_OPCODE.SEND, WR_OPCODE.RDMA_WRITE]
        if r_key is not None:
            assert wr_opcode in [WR_OPCODE.RDMA_WRITE, WR_OPCODE.RDMA_READ]
        if wr_opcode in [WR_OPCODE.RDMA_WRITE, WR_OPCODE.RDMA_READ]:
            assert r_key is not None

        self.wr_opcode = wr_opcode
        self.l_key   = l_key
        self.va      = va
        self.dma_len = dma_len
        self.ack_req = ack_req
        self.immdt   = immdt if immdt else 0
        self.w_immdt = (immdt is not None)
        self.r_key   = r_key if r_key else 0

    def post(self, wr_sink):
        yield wr_sink.wr_opcode.eq(self.wr_opcode)
        yield wr_sink.l_key.eq(self.l_key)
        yield wr_sink.va.eq(self.va)
        yield wr_sink.dma_len.eq(self.dma_len)
        yield wr_sink.ack_req.eq(self.ack_req)
        yield wr_sink.w_immdt.eq(self.w_immdt)
        yield wr_sink.immdt.eq(self.immdt)
        yield wr_sink.r_key.eq(self.r_key)

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

        self.mrs_handler = mr.LiteEthIBMemoryRegionsHandler()

        mem_num = 16
        # Memory regions used for RDMA WRITE requests
        wmems = []
        for i in range(mem_num // 2):
            wmem = Memory(16, 2**10) # 2**11 bytes
            wmem_port = wmem.get_port()
            wmem_mr = self.mrs_handler.reg_mr(
                region_start = 0,
                region_size  = 2**11,
                permissions  = mr.PERM(0),
                r_key        = i,
                l_key        = i,
                memory       = wmem,
                read_port    = wmem_port,
                write_port   = None
            )

            self.specials += wmem, wmem_port

            wmems.append(wmem_mr)

        # Memory regions used for RDMA READ requests
        rmems = []
        for i in range(mem_num // 2):
            rmem = Memory(16, 2**10) # 2**11 bytes
            rmem_port = rmem.get_port(write_capable=True)
            rmem_mr = self.mrs_handler.reg_mr(
                region_start = 0,
                region_size  = 2**11,
                permissions  = mr.PERM.LOCAL_WRITE | mr.PERM.NO_LOCAL_READ,
                r_key        = mem_num // 2 + i,
                l_key        = mem_num // 2 + i,
                memory       = rmem,
                read_port    = None,
                write_port   = rmem_port
            )

            self.specials += rmem, rmem_port

            rmems.append(rmem_mr)

        keymem_size = 2**log2_int(mem_num * 12 + 1, need_pow2=False)
        self.keymem = keymem = Memory(16, keymem_size // 2)
        keymem_port = keymem.get_port(write_capable=True)
        self.mrs_handler.reg_mr(
            region_start = 0,
            region_size  = keymem_size,
            permissions  = mr.PERM.LOCAL_WRITE | mr.PERM.NO_LOCAL_READ,
            r_key        = 0xdeaded,
            l_key        = 0xdeaded,
            memory       = keymem,
            read_port    = None,
            write_port   = keymem_port
        )

        self.specials += keymem, keymem_port

        cq = LiteEthCQ(depth=0x10)
        self.submodules.cq = cq

        qp = LiteEthIBQP(qp_id=0xdeaded)
        self.submodules += qp

        special_qp = LiteEthIBSpecialQP()
        self.submodules += special_qp

        self.qps = qps = [special_qp, qp]

        self.mad = mad = LiteEthIBMAD(qps, 10e6, dw=8)

        self.ip_dummy = ip_dummy = IPModule_dummy(dw=8)
        self.udp_dummy = udp_dummy = UDPModule_dummy(ip_dummy, dw=8)

        self.rocev2_tx = rocev2_tx = LiteEthIBTransportTX(ip_dummy, mad.tx, qps, self.mrs_handler.mrs, with_crc=True, buffered_out=True)
        self.rocev2_rx = rocev2_rx = LiteEthIBTransportRX(ip_dummy, rocev2_tx, mad.rx, qps, cq, self.mrs_handler.mrs, int(125e6), with_crc=True)

        self.crc_ins = crc_ins = LiteEthInfinibandICRCInserter(eth_mac_description(dw=8), eth_rocev2_description(dw=8))

        # Streamer
        rdma_streamer = LiteEthRDMAStreamer(qp, cq, wmems, rmems, 8)
        self.submodules.rdma_streamer = rdma_streamer

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

            ip_dummy.tx.source.ready.eq(1)
        ]


        ########

        self.comb += [
            mad.ipcm.source.ready.eq(1)
        ]

        from liteeth.core.rocev2.rdma_key_exchanger import LiteEthRDMAKeyExchanger
        self.key_exchanger = LiteEthRDMAKeyExchanger(
            qp       = qp,
            cq       = cq,
            keymem   = self.keymem,
            memr_num = mem_num,
            dw       = 8
        )

        self.fsm = fsm = FSM(reset_state="WAIT_RTS")
        fsm.act("WAIT_RTS",
            If(qp.qp_state == LiteEthIBQP.RTS,
                NextState("REQUEST")
            )
        )

        fsm.act("REQUEST",
            self.key_exchanger.request.eq(1),
            NextState("WAIT_KEYS")
        )

        fsm.act("WAIT_KEYS",
            If(self.key_exchanger.source.valid,
                NextState("R_KEYS")
            )
        )

        fsm.act("R_KEYS",
            self.key_exchanger.source.ready.eq(1),
            If(self.key_exchanger.source.valid,
                NextValue(self.rdma_streamer.r_keys[self.key_exchanger.source.key_cnt], self.key_exchanger.source.r_key),
                NextValue(self.rdma_streamer.vas[self.key_exchanger.source.key_cnt], self.key_exchanger.source.va),
                If(self.key_exchanger.source.last,
                    NextState("SEND")
                )
            )
        )


        cnt = Signal(8)

        fsm.act("SEND",
            self.rdma_streamer.enable.eq(1),

            NextValue(cnt, cnt + 1),
            self.rdma_streamer.sink.valid.eq(1),
            self.rdma_streamer.sink.data.eq(cnt),

            self.rdma_streamer.source.ready.eq(1),

            If(qp.qp_state != LiteEthIBQP.RTS,
                NextState("R_KEYS")
            )
        )

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

def receiver_send(dut):
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    print(1)
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.ReadyToUse)
    print(2)
    yield from dut.send_ib_packet(BTH_OPCODE_OP.SEND_Only)
    print(3)
    yield from dut.send_mad_packet(MAD_ATTRIB_ID.DisconnectRequest)

def receiver_direct(dut):
    packets = [
        "10e2d5000000207bd293899a080045000134200a400040119626c0aa0102c0aa0132d87212b7012000006400ffff00000001800000128001000000000001010702030000000000000002f2c97e400010000000000000407ec9f2000000000000000001061c06227bd2fffe93899a00000000000000000000150100000010000000a04b1dd4a7ffff37f0ffffffff00000000000000000000ffffc0aa010200000000000000000000ffffc0aa01324d8c60000040008800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040ab40000000000000000000000000c0aa0102000000000000000000000000c0aa01320000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000c962f010",
        "w256",
        "10e2d5000000207bd293899a080045000134200d400040119623c0aa0102c0aa0132d87212b7012000006400ffff00000001800000138001000000000001010702030000000000000002f2c97e400014000000000000407ec9f2fedcabed000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000092fdfef0",
        "w1024",
        "10e2d5000000207bd293899a0800450000ec2107400040119571c0aa0102c0aa0132d8d512b700d800000400ffff00deaded80fedfed000050fb000056d1cf3b830000005162000056d1cf3b8b1000005245000056d1cf3b932000005309000056d1cf3b9b3000005450000056d1cf3ba3400000559b000056d1cf3bab50000056ce000056d1cf3bb360000057ac000056d1cf3bbb700000584a000056d1cf3bc38000005933000056d1cf3bcb9000005a69000056d1cf3bd3a000005be3000056d1cf3bdbb000005c23000056d1cf3be3c000005dc9000056d1cf3bebd000005e6d000056d1cf3bf3e000005f59000056d1cf3bfbf0836fdfe2",
        "w2000",
        "10e2d5000000207bd293899a08004500013422ca400040119366c0aa0102c0aa0132d87212b7012000006400ffff00000001800000148001000000000001010702030000000000000002f2c97e400015000000000000407ec9f2fedcabeddeaded0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000fc813e59",
        "w3000"
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
                yield from dut.send_direct_stream(bytes.fromhex(packetdata[28:-8]))
                print(f"Sent packet number {packetn}")
                packetn += 1

        print("Waiting for completion")
        for _ in range(2000):
            yield
        print("Done!")

        yield dut.direct_bytestream.eq(0)
        yield

def sender_send(dut):
    wr = WR(WR_OPCODE.RDMA_WRITE, 0x11111111, 0x0, 0x100, 0b0, r_key=0x11111111)
    yield from wr.post(dut.qps[1].send_queue.sink)
    while (yield dut.qps[1].qp_state) != LiteEthIBQP.RTS:
        yield
    yield dut.qps[1].send_queue.sink.valid.eq(1)
    yield
    yield dut.qps[1].send_queue.sink.valid.eq(0)
    yield

class TestRoCEv2(unittest.TestCase):
    def test_exchange(self):
        dut = DUT()

        run_simulation(dut, [receiver_send(dut), record_msg(dut)], vcd_name="test.vcd")

    def test_direct(self):
        dut = DUT()

        run_simulation(dut, [receiver_direct(dut), sender_send(dut)], vcd_name="test.vcd")
