from migen import *
from liteeth.core.rocev2.rocev2 import LiteEthIBTransportRX, LiteEthIBTransportTX, LiteEthIBQP, LiteEthIBSpecialQP, LiteEthIBTransportPacketizer
from liteeth.core.rocev2.mad_cm import LiteEthIBMAD, LiteEthCMPacketizer
from litex.gen import *

from litex.soc.interconnect import stream

from liteeth.common import *

class LiteEthInfinibandMemoryRegionReaderDummy(LiteXModule):
    def __init__(self, region_start, region_size, port, base_address, dw=8):
        self.sink = sink = stream.Endpoint([("va", 64), ("len", 6)])
        self.source = source = stream.Endpoint([("data", dw), ("error", 1)])

        self.inner_reader_sink   = inner_reader_sink   = stream.Endpoint([("address", port.address_width)])
        self.inner_reader_source = inner_reader_source = stream.Endpoint([("data", port.data_width)])

        running_address = Signal(port.address_width)
        added_chunks = Signal(6)

        self.fsm = fsm = FSM(reset_state="IDLE")
        self.fsm_indicator = fsm_indicator = Signal()
        fsm.act("IDLE",
            fsm_indicator.eq(0),
            sink.ready.eq(1),
            If(sink.valid & (~source.error),
                sink.ready.eq(0),
                NextValue(running_address, region_start + sink.va),
                NextValue(added_chunks, 1),
                NextState("READING")
            )
        )

        fsm.act("READING",
            fsm_indicator.eq(1),
            inner_reader_sink.address.eq(base_address + running_address),
            inner_reader_sink.valid.eq(1),
            If(inner_reader_sink.ready,
                NextValue(running_address, running_address + 1),
                NextValue(added_chunks, added_chunks + 1),
                If(added_chunks == sink.len,
                    inner_reader_sink.last.eq(1),
                    sink.ready.eq(1), # Consume incoming
                    NextState("IDLE")
                )
            )
        )

        self.comb += source.error.eq(sink.va > region_size | ((sink.va + sink.len) > region_size))
        self.comb += inner_reader_source.connect(source)


class LiteEthInfinibandMemoryRegionWriterDummy(LiteXModule):
    def __init__(self, region_start, region_size, port, base_address, dw=8):
        self.sink = sink = stream.Endpoint(stream.EndpointDescription([("data", dw)], [("va", 64)]))
        self.source = source = stream.Endpoint([("error", dw)])

        self.inner_writer_sink = inner_writer_sink = stream.Endpoint([("address", port.address_width),
                                            ("data", port.data_width)])

        running_address = Signal(port.address_width)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid & (~source.error),
                NextValue(running_address, region_start + sink.va),
                NextState("WRITING")
            )
        )

        fsm.act("WRITING",
            inner_writer_sink.data.eq(sink.data),
            inner_writer_sink.address.eq(base_address + running_address),
            inner_writer_sink.valid.eq(sink.valid),
            sink.ready.eq(inner_writer_sink.ready),
            If(sink.valid & sink.ready,
                NextValue(running_address, running_address + 1),
                If(sink.last,
                    NextState("IDLE")
                )
            )
        )
        self.comb += source.error.eq(sink.va > region_size)

class LiteEthInfinibandMemoryRegionDummy(LiteXModule):
    def __init__(self, region_start, region_size, port, dw=8):
        self.reader = reader = LiteEthInfinibandMemoryRegionReaderDummy(region_start, region_size, port, 0, dw)
        self.writer = writer = LiteEthInfinibandMemoryRegionWriterDummy(region_start, region_size, port, 0, dw)

        self.r_key = 0xdeadbeef


from collections import namedtuple
dummy_udp_port = namedtuple("udp_port", "address_width data_width")
dummy_udp_port.address_width = 24
dummy_udp_port.data_width = 8

class IPModule_dummy(LiteXModule):
    class IPTX(LiteXModule):
        def __init__(self, dw):
            self.source = stream.Endpoint(eth_mac_description(dw))

            self.source.data.eq(0)
            self.source.last_be.eq(0)
            self.source.error.eq(0)

    class IPRX(LiteXModule):
        def __init__(self, dw):
            self.sink = stream.Endpoint(eth_mac_description(dw))

            self.sink.data.eq(0)
            self.sink.last_be.eq(0)
            self.sink.error.eq(0)

    def __init__(self, dw):
        self.rx = self.IPRX(dw)

class TEST_IBT(LiteXModule):
    def __init__(self):
        # Packetizer will feed into the rocev2 module as if packets were being received through udp
        self.mad_pack = mad_pack = LiteEthCMPacketizer(dw=8)
        self.ib_pack = ib_pack = LiteEthIBTransportPacketizer(dw=8)
        self.direct_ib_sink = direct_ib_sink = stream.Endpoint(eth_rocev2_description(dw=8))
        self.mad_packet = Signal()

        # The memory region is replaced with a dummy
        memory_region = LiteEthInfinibandMemoryRegionDummy(0, 1024, dummy_udp_port)
        qp = LiteEthIBQP(id=0xdeaded, send_depth=8, receive_depth=8)
        qp.memory_region = memory_region
        self.submodules += qp

        special_qp = LiteEthIBSpecialQP(send_depth=8, receive_depth=8)
        self.submodules += special_qp

        self.qps = qps = [special_qp, qp]

        self.mad = mad = LiteEthIBMAD(qps, dw=8)

        self.ip_dummy = ip_dummy = IPModule_dummy(dw=8)

        self.rocev2_tx = rocev2_tx = LiteEthIBTransportTX(ip_dummy, mad.tx, qps, with_crc=False, buffered_out=True)
        self.rocev2_rx = rocev2_rx = LiteEthIBTransportRX(ip_dummy, rocev2_tx.ack_sink, rocev2_tx.read_sink, mad.rx, qps, with_crc=False)

        self.comb += [
            rocev2_tx.enable.eq(1),
            If(self.mad_packet,
                mad_pack.source.connect(ib_pack.sink, keep={"valid", "ready", "last", "data"})
            ).Else(
                direct_ib_sink.connect(ib_pack.sink, keep={"valid", "ready", "last", "data"})
            ),
            ib_pack.source.connect(rocev2_rx.sink),
            rocev2_rx.source.ready.eq(1),
            rocev2_tx.source.ready.eq(1),
        ]

test_rocev2 = TEST_IBT()


bank_size = 1024
address_pipe = [(0, 0)] * 256
pipe_read = 0
pipe_write = 0
bank = [0] * bank_size
def memory_region_dummy_act():
    global bank
    mem_region = test_rocev2.qps[1].memory_region
    # Read
    global pipe_read
    global pipe_write
    if (yield mem_region.reader.inner_reader_sink.valid):
        address_pipe[pipe_read] = ((yield mem_region.reader.inner_reader_sink.address), (yield mem_region.reader.inner_reader_sink.last))
        assert address_pipe[pipe_read][0] >= 0 and address_pipe[pipe_read][0] < bank_size
        pipe_read = (pipe_read + 1) % 256

    yield mem_region.reader.inner_reader_sink.ready.eq(1)

    source_ready = (yield mem_region.reader.inner_reader_source.ready)
    source_valid = (yield mem_region.reader.inner_reader_source.valid)
    source_last = (yield mem_region.reader.inner_reader_source.last)

    if source_last:
        if source_ready and source_valid:
            yield mem_region.reader.inner_reader_source.valid.eq(0)
            yield mem_region.reader.inner_reader_source.last.eq(0)
    else:
        if source_ready and source_valid:
            pipe_write = (pipe_write + 1) % 256
            yield mem_region.reader.inner_reader_source.data.eq(bank[address_pipe[pipe_write][0]])
            yield mem_region.reader.inner_reader_source.last.eq(bank[address_pipe[pipe_write][1]])
            yield mem_region.reader.inner_reader_source.valid.eq(1)
        elif source_ready:
            yield mem_region.reader.inner_reader_source.valid.eq(1)
            yield mem_region.reader.inner_reader_source.data.eq(bank[address_pipe[pipe_write][0]])
            yield mem_region.reader.inner_reader_source.last.eq(bank[address_pipe[pipe_write][1]])

    # Write
    addr = (yield mem_region.writer.inner_writer_sink.address)

    assert addr >= 0 and addr < bank_size

    yield mem_region.writer.inner_writer_sink.ready.eq(1)
    bank[addr] = (yield mem_region.writer.inner_writer_sink.data)

from random import randint


# Replaces yield to do side operations on every clock front
def tick():
    yield from memory_region_dummy_act()
    if ((yield test_rocev2.ib_pack.source.ready) and (yield test_rocev2.ib_pack.source.valid)):
        add_byte((yield test_rocev2.ib_pack.source.data))
    yield

choices = {}
def random_signal(signal, signal_name=""):
    global choices
    l = len(signal)
    r = randint(0, (1 << l) - 1)
    choices[signal_name] = r
    yield signal.eq(r)

def set_mad_packet_params(attrib):
    # MAD
    yield test_rocev2.mad_pack.sink.BaseVersion.eq(0x01)
    yield test_rocev2.mad_pack.sink.MgmtClass.eq(0x07)
    yield test_rocev2.mad_pack.sink.ClassVersion.eq(0x02)
    yield test_rocev2.mad_pack.sink.Method.eq(0x03)
    yield test_rocev2.mad_pack.sink.AttributeID.eq(attrib)

    # CM
    yield test_rocev2.mad_pack.sink.Local_QPN.eq(0x11)
    yield test_rocev2.mad_pack.sink.Partition_Key.eq(0xffff)
    yield test_rocev2.mad_pack.sink.Starting_PSN.eq(0xcafeed)
    yield test_rocev2.mad_pack.sink.Primary_Local_Port_GID.eq(convert_ip("192.168.1.1"))

    global qp_psn
    qp_psn = 0xcafeed

sp_qp_psn = 0
qp_psn = 0
def set_ib_packet_params(opcode, psn, dest_qp=0xdeaded, length=PMTU):
    # Set opcode
    yield test_rocev2.ib_pack.sink.opcode.eq(opcode)
    yield test_rocev2.ib_pack.sink.psn.eq(psn)
    yield test_rocev2.ib_pack.sink.tver.eq(0)
    yield test_rocev2.ib_pack.sink.dest_qp.eq(dest_qp)
    yield test_rocev2.ib_pack.sink.pad.eq((4 - (length % 4)) % 4)

    yield test_rocev2.ib_pack.sink.se.eq(0)
    yield test_rocev2.ib_pack.sink.m.eq(0)
    yield test_rocev2.ib_pack.sink.a.eq(1)
    yield test_rocev2.ib_pack.sink.se.eq(0)
    yield test_rocev2.ib_pack.sink.p_key.eq(0xffff)

    if dest_qp == 1:
        yield test_rocev2.ib_pack.sink.q_key.eq(CM_Q_Key)
        yield test_rocev2.ib_pack.sink.src_qp.eq(1)

    # # Randomize parameters
    # for p in test_rocev2.pack.sink.description.param_layout:
    #     if p[0] != "opcode" and p[0] != "r_key" and p[0] != "psn" and p[0] != "tver"and p[0] != "dest_qp" and p[0] != "pad":
    #         yield from random_signal(getattr(test_rocev2.pack.sink, p[0]), signal_name=p[0])
    psn += 1

def set_rdma_params():
    virtual_address = 0
    dma_len = 10
    yield test_rocev2.ib_pack.sink.va.eq(virtual_address)
    yield test_rocev2.ib_pack.sink.r_key.eq(0xdeadbeef)
    yield test_rocev2.ib_pack.sink.dma_len.eq(dma_len)

def set_packet_data(data):
    yield test_rocev2.direct_ib_sink.data.eq(data)

out = ""
def add_byte(n):
    global out
    if n < 0 or n > 255:
        raise ValueError
    out += f"\\x{n:02x}"

# Configures Infiniband transport layer packetizer
def setup_ib(opcode, psn, dest_qp=0xdeaded, length=PMTU):
    header_only = (length == 0)
    yield from set_ib_packet_params(opcode, psn, dest_qp, length)

    opcode_conn_type = (opcode & 0b11100000) >> 5
    opcode_op        = opcode & 0b00011111
    if opcode_op in [0b00110, 0b00111, 0b01000, 0b01001, 0b01010, 0b01100]:
        yield from set_rdma_params()

    yield test_rocev2.ib_pack.header_only.eq(header_only)

# Sends an Infiniband transport layer packet
def ib_packet(opcode, dest_qp=0xdeaded, length=PMTU):
    header_only = (length == 0)
    yield test_rocev2.mad_packet.eq(0)
    global qp_psn
    yield from setup_ib(opcode, qp_psn, dest_qp=dest_qp, length=length)
    qp_psn += 1

    yield test_rocev2.direct_ib_sink.valid.eq(1)
    i = 0
    if not header_only:
        yield from set_packet_data(i)
    yield from tick()

    while (yield test_rocev2.direct_ib_sink.ready) == 0:
        yield from tick()

    if not header_only:
        while i < length - 1:
            if (yield test_rocev2.direct_ib_sink.ready) == 1:
                i += 1
                yield from set_packet_data(i)
                if i == length - 1:
                    yield test_rocev2.direct_ib_sink.last.eq(1)
            yield from tick()
        yield test_rocev2.direct_ib_sink.last.eq(0)
    yield test_rocev2.direct_ib_sink.valid.eq(0)
    yield from tick()

# Sends a MAD Communication Management packet
def mad_packet(attrib):
    length = 256
    yield test_rocev2.mad_packet.eq(1)

    # Setup MAD and Transport layer packetizers
    yield from set_mad_packet_params(attrib)
    global sp_qp_psn
    yield from setup_ib(BTH_OPCODE.UD.SEND_Only, sp_qp_psn, dest_qp=1, length=length)
    sp_qp_psn += 1
    yield test_rocev2.mad_pack.header_only.eq(0)

    # Turn on packetizer
    i = 0
    yield test_rocev2.mad_pack.sink.data.eq(i)
    yield test_rocev2.mad_pack.sink.valid.eq(1)
    yield from tick()

    # Wait for packetizer to start accepting data
    while (yield test_rocev2.mad_pack.sink.ready) == 0:
        if (yield test_rocev2.mad_pack.source.ready) and (yield test_rocev2.mad_pack.source.valid):
            i += 1
        yield from tick()

    # Send data
    while i < length - 1:
        if (yield test_rocev2.mad_pack.sink.ready):
            i += 1
            yield test_rocev2.mad_pack.sink.data.eq(i)
            if i == length - 1:
                yield test_rocev2.mad_pack.sink.last.eq(1)
        yield from tick()

    # Turn off packetizer
    yield test_rocev2.mad_pack.sink.last.eq(0)
    yield test_rocev2.mad_pack.sink.valid.eq(0)
    yield test_rocev2.mad_packet.eq(0)
    yield from tick()

def testbench():
    global psn
    global out
    yield test_rocev2.rocev2_rx.source.ready.eq(1)
    # yield from packet(0b00000)
    # yield from packet(0b00001)
    # yield from packet(0b00000)
    # psn -= 1
    # yield from packet(0b00011)

    # yield from packet(0b00110)
    # yield from packet(0b00110)
    # psn -= 1
    # yield from packet(0b00111)
    # psn -= 1
    # yield from packet(0b01000)
    # psn -= 1
    # yield from packet(0b00110)
    # yield from mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    yield from mad_packet(MAD_ATTRIB_ID.ConnectRequest)
    print(out + "\n"); out = ""
    yield from ib_packet(BTH_OPCODE.RC.RDMA_WRITE_Only, length=10)
    print(out + "\n"); out = ""
    yield from ib_packet(BTH_OPCODE.RC.RDMA_READ_Request, length=0)
    # yield from ib_packet(BTH_OPCODE.UD.SEND_Only, dest_qp=1)
    print(out + "\n"); out = ""
    yield from mad_packet(MAD_ATTRIB_ID.ReadyToUse)
    print(out + "\n"); out = ""
    # yield from packet(0b00110)

    # yield from packet(0b00100, length=4)
    for i in range(300):
        yield from tick()

    yield from ib_packet(BTH_OPCODE.RC.RDMA_WRITE_Only, length=256)
    print(out + "\n"); out = ""
    yield from ib_packet(BTH_OPCODE.RC.RDMA_READ_Request, length=0)
    # yield from ib_packet(BTH_OPCODE.UD.SEND_Only, dest_qp=1)
    print(out + "\n"); out = ""
    yield from mad_packet(MAD_ATTRIB_ID.DisconnectRequest)
    print(out + "\n"); out = ""

    for i in range(200):
        yield from tick()


run_simulation(test_rocev2, testbench(), vcd_name="test.vcd")

from migen.fhdl.verilog import convert
convert(TEST_IBT()).write("my_design.v")
