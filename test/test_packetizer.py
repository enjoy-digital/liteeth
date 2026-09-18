import unittest

from migen import *
from litex.gen import *

from liteeth.common import *
from liteeth.core.rocev2.common import *

class Feedback(LiteXModule):
    def __init__(self, outer_description, inner_description):
        self.pack = pack = VariablePacketizer(outer_description, inner_description, IBT_headers, IBT_opmap)
        self.depack = depack = VariableDepacketizer(inner_description, outer_description, IBT_headers, IBT_opmap)

        self.comb += pack.source.connect(depack.sink)

choices = {}

from random import randint

def random_signal(signal, signal_name=""):
    global choices
    l = len(signal)
    r = randint(0, (1 << l) - 1)
    choices[signal_name] = r
    yield signal.eq(r)

def set_packet_params(dut, opcode):
    # Set opcode
    yield dut.pack.sink.opcode.eq(opcode)

    # Randomize parameters
    for p in dut.pack.sink.description.param_layout:
        if p[0] != "opcode" and p[0] != "header_only":
            yield from random_signal(getattr(dut.pack.sink, p[0]), signal_name=p[0])



cycler = 1
def set_packet_data(dut):
    global cycler
    yield dut.pack.sink.data.eq(cycler)
    cycler = 1 + cycler % 3

def packet(dut, opcode, header_only=False):
    yield dut.pack.sink.valid.eq(1)
    yield dut.depack.source.ready.eq(1)

    yield from set_packet_params(dut, opcode)

    if not header_only:
        yield from set_packet_data(dut)
    yield

    while True:
        if (yield dut.pack.sink.ready) == 1:
            if not header_only:
                yield from set_packet_data(dut)
                yield
                yield from set_packet_data(dut)
                yield dut.pack.sink.last.eq(1)
                yield
            yield dut.pack.sink.valid.eq(0)
            yield dut.pack.sink.last.eq(0)
            while (yield dut.depack.source.valid) != 1:
                yield
            yield from check_received_packet(dut, opcode)
            break
        yield

def check_received_packet(dut, opcode):
    global choices
    valid_headers = [bth_header] + [header for i, header in list(enumerate(IBT_headers))[1:] if IBT_opmap[opcode] & (1 << (i - 1))]

    for signame, v in choices.items():
        validsig = False
        for header in valid_headers:
            for p, _ in header.get_layout():
                if p == signame:
                    validsig = True
        if validsig:
            x = yield getattr(dut.depack.source, signame)
            if x != v:
                print(choices)
                print(signame, x)
            assert (x) == v

def send_packets(dut):
    header_only = False
    for opcode in IBT_RC_OPS + IBT_UD_OPS:
        if opcode in [0b01100, 0b10001, 0b10010, 0b10011, 0b10100]:
            yield dut.pack.sink.header_only.eq(1)
            header_only = True
        else:
            yield dut.pack.sink.header_only.eq(0)
            header_only = False
        yield from packet(dut, opcode, header_only)
        yield

class TestStream(unittest.TestCase):
    dw = 8
    def test_feedback(self):
        dut = Feedback(eth_rocev2_description(self.dw), eth_udp_description(self.dw))

        run_simulation(dut, send_packets(dut), vcd_name="test.vcd")
