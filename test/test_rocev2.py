import unittest

from migen import *
from litex.gen import *

from litex.soc.interconnect.stream import Endpoint, EndpointDescription

from liteeth.common import *

from liteeth.core.rocev2.qp import LiteEthIBQP, LiteEthIBSpecialQP
from liteeth.core.rocev2.cq import LiteEthCQ
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

        self.mr_handler = mr_handler = mr.LiteEthIBMemoryRegionsHandler()

        mem_num = 8
        total_mem_size = 0x800 * mem_num
        self.wmem = wmem = mr.LiteEthDummyTXMR(total_mem_size // 2, 8)
        self.wmem = wmem = ClockDomainsRenamer({
            "sys": "sys",
            "write": "sys",
            "read": "sys"
        })(wmem)
        # Memory regions used for RDMA WRITE requests
        wmem_port = wmem.get_read_port()
        wmem_mr = mr_handler.reg_mr(
            region_start = 0,
            region_size  = total_mem_size // 2,
            permissions  = mr.PERM(0),
            r_key        = 0x11111111,
            l_key        = 0x11111111,
            read_port    = wmem_port,
            write_port   = None
        )
        wmem_l_key = wmem_mr.l_key

        rmem = mr.LiteEthDummyRXMR(total_mem_size // 2, 8)
        self.rmem = rmem = ClockDomainsRenamer({
            "sys": "sys",
            "write": "sys",
            "read": "sys"
        })(rmem)
        # Memory regions used for RDMA READ requests
        rmem_port = rmem.get_write_port()
        rmem_mr = mr_handler.reg_mr(
            region_start = 0,
            region_size  = total_mem_size // 2,
            permissions  = mr.PERM.LOCAL_WRITE | mr.PERM.NO_LOCAL_READ,
            r_key        = 0x22222222,
            l_key        = 0x22222222,
            read_port    = None,
            write_port   = rmem_port
        )
        rmem_l_key = rmem_mr.l_key

        keymem_size = 2**log2_int(mem_num * 12 + 1, need_pow2=False)
        self.keymem = keymem = Memory(16, keymem_size // 2)
        keymem_port = keymem.get_port(write_capable=True)
        mr_handler.reg_mr(
            region_start = 0,
            region_size  = keymem_size,
            permissions  = mr.PERM.LOCAL_WRITE | mr.PERM.NO_LOCAL_READ,
            r_key        = 0xdeaded,
            l_key        = 0xdeaded,
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

        self.rocev2_tx = rocev2_tx = LiteEthIBTransportTX(ip_dummy, mad.tx, qps, mr_handler.mrs, with_crc=True, buffered_out=True)
        self.rocev2_rx = rocev2_rx = LiteEthIBTransportRX(ip_dummy, rocev2_tx, mad.rx, qps, cq, mr_handler.mrs, int(125e6), with_crc=True)

        self.crc_ins = crc_ins = LiteEthInfinibandICRCInserter(eth_mac_description(dw=8), eth_rocev2_description(dw=8))

        qp_port = qp.crossbar.get_port(qp_id = "0xdeaded", depth=16)
        cq_port = cq.crossbar.get_port(qp_id = "0xdeaded", depth=16)

        # Streamer
        rdma_streamer = LiteEthRDMAStreamer(qp_port, cq_port, wmem, rmem, wmem_l_key, rmem_l_key, mem_num, 8)
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
            qp       = qp_port,
            cq       = cq_port,
            keymem   = self.keymem,
            mem_num = mem_num,
            dw       = 8
        )

        reset = Signal(reset=1)
        self.sync += reset.eq(0)
        self.comb += [
            self.rmem.reset_sys.eq(reset),
            self.wmem.reset_sys.eq(reset),
            self.rdma_streamer.reset.eq(reset),
            self.key_exchanger.reset.eq(reset)
        ]

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
                self.key_exchanger.source.connect(self.rdma_streamer.key_sink, keep={"valid", "ready", "r_key", "va"}),
                If(self.key_exchanger.source.last,
                    NextState("SEND")
                )
            )
        )


        cnt = Signal(8)

        fsm.act("SEND",
            self.rdma_streamer.enable.eq(1),

            # NextValue(cnt, cnt + 1),
            # self.rdma_streamer.sink.valid.eq(1),
            # self.rdma_streamer.sink.data.eq(cnt),

            # self.rdma_streamer.source.ready.eq(1),

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

        yield self.ib_pack.sink.header_only.eq(header_only)

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
        yield self.mad_pack.sink.header_only.eq(0)

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
    vlan = True
    crc = True
    packet_start = 28 + (8 if vlan else 0)
    packet_end = (-8 if crc else 0)

    packets = [
        "10e2d5000000b8599f077e2a810000010800450201349d404000401118eec0aa0102c0aa0132fa4f12b7012000006440ffff00000001000001cc800100000000000101070203000000000000000357feb735001000000000000035b7fe57000015b30000000001066b13b8599f0300077e2a00000000000000000001441000000010000000b0cf7bf6b7ffff37f0ffffffff00000000000000000000ffffc0aa010200000000000000000000ffffc0aa01325ba590000040009000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040affa000000000000000000000000c0aa0102000000000000000000000000c0aa013200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002fcc9e99",
        "w256",
        "10e2d5000000b8599f077e2a810000010800450201349d414000401118edc0aa0102c0aa0132fa4f12b7012000006440ffff00000001000001cd800100000000000101070203000000000000000357feb735001400000000000035b7fe57fedcabed0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000a286b250",
        "w256",
        "10e2d5000000b8599f077e2a810000010800450200ec9d4d400040111929c0aa0102c0aa0132fa4f12b700d800000440ffff00deaded80fedfed001eeac8000055f5d63c3020001eecca000055f5d63c3820001eeecc000055f5d63c4020001ef0ce000055f5d63c4820001ef2d0000055f5d63c5020001ef4d2000055f5d63c5820001ef6d4000055f5d63c6020001ef8d6000055f5d63c6820001efad8000055f5d63c7030001efcda000055f5d63c7830001efedc000055f5d63c8030001f00de000055f5d63c8830001f02e0000055f5d63c9030001f04e2000055f5d63c9830001f06e4000055f5d63ca030001f08e6000055f5d63ca8301e083ed6",
        "w1024",
        "10e2d5000000b8599f077e2a810000010800450200309d4e4000401119e4c0aa0102c0aa0132fa4f12b7001c00001140ffff00deaded00cf7bf70000000259caeaf7",
        "10e2d5000000b8599f077e2a810000010800450200309d4f4000401119e3c0aa0102c0aa0132fa4f12b7001c00001140ffff00deaded00cf7bf900000004a4da30ef"
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
                yield from dut.send_direct_stream(bytes.fromhex(packetdata[packet_start:packet_end]))
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
