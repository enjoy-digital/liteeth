import unittest
from litex.gen import *

from litex.soc.interconnect.stream import EndpointDescription
from liteeth.core.rocev2.common import WaitPipe

PMTU = 256

class Packet:
    def __init__(self, data=None, param=0):
        self.data = data if data else []
        self.param = param
        self.length = len(data)

    def cmp(self, other):
        return (self.data == other.data
            and self.param == other.param
            and self.length == other.length)

    def cmp_headers(self, other):
        return (self.param == other.param)

def push_packet(dut, packet, valid):
    print(f"Sending packet {packet.data}")
    yield dut.sink.valid.eq(1)
    yield dut.sink.parameter.eq(packet.param)

    if packet.length == 0:
        yield dut.sink.last.eq(1)
        yield
    else:
        i = 0
        while i < packet.length:
            yield dut.sink.data.eq(packet.data[i])
            yield dut.sink.last.eq(i == packet.length - 1)
            yield
            if (yield dut.sink.valid) and (yield dut.sink.ready):
                i += 1
    yield dut.sink.last.eq(0)

    yield dut.sink.valid.eq(0)
    if valid:
        yield dut.validate.eq(1)
        yield dut.invalidate.eq(0)
    else:
        yield dut.validate.eq(0)
        yield dut.invalidate.eq(1)
    yield

    yield dut.validate.eq(0)
    yield dut.invalidate.eq(0)

def pop_packet(dut, packet=None, header_only=False):
    yield dut.header_only.eq(header_only)
    yield dut.source.ready.eq(1)
    yield

    param = (yield dut.source.parameter)
    length = (yield dut.length)
    if packet:
        packet.param = param
        packet.length = length

    if not header_only:
        i = 0
        while i < length:
            data = (yield dut.source.data)
            if (yield dut.source.valid) and (yield dut.source.ready):
                if (yield dut.source.last):
                    packet.length = i + 1
                if packet:
                    packet.data.append(data)
                i += 1
            yield

    yield dut.source.ready.eq(0)

def push_packets(dut, packets, valid_map=None, delay=0):
    valid_map = valid_map if valid_map else [True] * len(packets)
    assert len(valid_map) == len(packets)

    for i, packet in enumerate(packets):
        yield from push_packet(dut, packet, valid_map[i])
        while (yield dut.full):
            yield from push_packet(dut, packet, valid_map[i])

        if i != len(packets) - 1:
            for _ in range(delay):
                yield

def pop_packets(dut, packets, n=1, header_only_map=None, delay=0):
    header_only_map = header_only_map if header_only_map else [False] * n
    assert len(header_only_map) == n

    c = 0
    while c < n:
        if (yield dut.source.valid):
            packets.append(Packet([]))
            yield from pop_packet(dut, packets[-1], header_only=header_only_map[c])
            if c != n - 1:
                for _ in range(delay):
                    yield
            c += 1
        yield

class TestWaitPipe(unittest.TestCase):
    dw = 8
    def test_wait_pipe(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10)])
        dut = WaitPipe(layout, 3, PMTU, self.dw)

        valid_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(PMTU - (9 - j) * 5)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, valid_map), pop_packets(dut, rcv_packets, sum(valid_map, 0))], vcd_name="test.vcd")
        sent_packets = [packet for (packet, valid) in zip(send_packets, valid_map) if valid]
        for s, r in zip(sent_packets, rcv_packets):
            self.assertTrue(s.cmp(r))

    def test_wait_pipe_full(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10)])
        dut = WaitPipe(layout, 3, PMTU, self.dw)

        valid_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(PMTU - (9 - j) * 5)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, valid_map, delay=0), pop_packets(dut, rcv_packets, sum(valid_map, 0), delay=PMTU * 5)], vcd_name="test.vcd")
        sent_packets = [packet for (packet, valid) in zip(send_packets, valid_map) if valid]
        for s, r in zip(sent_packets, rcv_packets):
            self.assertTrue(s.cmp(r))

    def test_wait_pipe_header_only(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10)])
        dut = WaitPipe(layout, 3, PMTU, self.dw)

        header_only_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(0 if header_only_map[j - 1] else 100)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets), pop_packets(dut, rcv_packets, len(send_packets), header_only_map)], vcd_name="test.vcd")
        for i, (s, r) in enumerate(zip(send_packets, rcv_packets)):
            if header_only_map[i]:
                self.assertTrue(s.cmp_headers(r))
            else:
                self.assertTrue(s.cmp(r))

if __name__ == "__main__":
    unittest.main()
