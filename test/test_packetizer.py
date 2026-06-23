from litex.soc.interconnect.packet import Header, HeaderField
from migen import *

from litex.soc.interconnect.stream import EndpointDescription

from liteeth.common import *

from liteeth.core.rocev2.common import *

from litex.gen import *

class Feedback(LiteXModule):
    def __init__(self, outer_description, inner_description):
        self.pack = pack = VariablePacketizer(outer_description, inner_description, IBT_headers, IBT_opmap)
        self.depack = depack = VariableDepacketizer(inner_description, outer_description, IBT_headers, IBT_opmap)

        self.comb += pack.source.connect(depack.sink)

feedback = Feedback(eth_rocev2_description(8), eth_udp_description(8))

choices = {}

from random import randint

def random_signal(signal, signal_name=""):
    global choices
    l = len(signal)
    r = randint(0, (1 << l) - 1)
    choices[signal_name] = r
    yield signal.eq(r)

def set_packet_params(opcode):
    # Set opcode
    yield feedback.pack.sink.opcode.eq(opcode)

    # Randomize parameters
    for p in feedback.pack.sink.description.param_layout:
        if p[0] != "opcode":
            yield from random_signal(getattr(feedback.pack.sink, p[0]), signal_name=p[0])



cycler = 1
def set_packet_data():
    global cycler
    yield feedback.pack.sink.data.eq(cycler)
    cycler = 1 + cycler % 3

def packet(opcode, header_only=False):
    yield from set_packet_params(opcode)

    if not header_only:
        yield from set_packet_data()

    yield feedback.pack.sink.valid.eq(1)
    yield feedback.depack.source.ready.eq(1)
    yield

    while True:
        if (yield feedback.pack.sink.ready) == 1:
            if not header_only:
                yield from set_packet_data()
                yield
                yield from set_packet_data()
                yield feedback.pack.sink.last.eq(1)
                yield
            yield feedback.pack.sink.valid.eq(0)
            yield feedback.pack.sink.last.eq(0)
            while (yield feedback.depack.source.valid) != 1:
                yield
            yield from check_received_packet(opcode)
            break
        yield

def check_received_packet(opcode):
    global choices
    valid_headers = [bth_header] + [header for i, header in list(enumerate(IBT_headers))[1:] if IBT_opmap[opcode] & (1 << (i - 1))]

    for signame, v in choices.items():
        validsig = False
        for header in valid_headers:
            for p, _ in header.get_layout():
                if p == signame:
                    validsig = True
        if validsig:
            print(signame, v)
            x = yield getattr(feedback.depack.source, signame)
            assert (x) == v

def testbench():
    header_only = False
    for opcode in IBT_RC_OPS + IBT_UD_OPS:
        if opcode in [0b01100, 0b10001, 0b10010, 0b10011, 0b10100]:
            yield feedback.pack.header_only.eq(1)
            header_only = True
        else:
            yield feedback.pack.header_only.eq(0)
            header_only = False
        yield from packet(opcode, header_only)
        yield

run_simulation(feedback, testbench(), vcd_name="test.vcd")

# from migen.fhdl.verilog import convert
# convert(Feedback(eth_rocev2_description(8), eth_udp_description(8))).write("test.v")
