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
        check = (self.data == other.data
            and self.param == other.param
            and self.length == other.length)
        if not check:
            if self.data != other.data:
                print("Data differs!")
            if self.param != other.param:
                print("Param differs!")
            if self.length != other.length:
                print("Length differs!")
        return check

    def cmp_headers(self, other):
        return (self.param == other.param)

def push_packet(dut, packet, valid, validate_delay):
    #print(f"Sending packet {packet.data}")
    yield dut.sink.valid.eq(1)
    yield dut.sink.parameter.eq(packet.param)

    if validate_delay > 0:
        if packet.length == 0:
            yield dut.sink.last.eq(1)
            yield dut.sink.header_only.eq(1)
            while not ((yield dut.sink.valid) and (yield dut.sink.ready)):
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
        for _ in range(validate_delay - 1):
            yield
        if valid:
            yield dut.validate_sink.validate.eq(1)
            yield dut.validate_sink.invalidate.eq(0)
        else:
            yield dut.validate_sink.validate.eq(0)
            yield dut.validate_sink.invalidate.eq(1)
        yield
    else:
        if packet.length == 0:
            if valid:
                yield dut.validate_sink.validate.eq(1)
                yield dut.validate_sink.invalidate.eq(0)
            else:
                yield dut.validate_sink.validate.eq(0)
                yield dut.validate_sink.invalidate.eq(1)

            yield dut.sink.last.eq(1)
            yield dut.sink.header_only.eq(1)
            yield
            while not ((yield dut.sink.valid) and (yield dut.sink.ready)):
                yield
        else:
            i = 0
            while i < packet.length:
                if i == packet.length - 1:
                    if valid:
                        yield dut.validate_sink.validate.eq(1)
                        yield dut.validate_sink.invalidate.eq(0)
                    else:
                        yield dut.validate_sink.validate.eq(0)
                        yield dut.validate_sink.invalidate.eq(1)
                yield dut.sink.data.eq(packet.data[i])
                yield dut.sink.last.eq(i == packet.length - 1)
                yield
                if (yield dut.sink.valid) and (yield dut.sink.ready):
                    i += 1
        yield dut.sink.last.eq(0)
        yield dut.sink.valid.eq(0)

    yield dut.validate_sink.validate.eq(0)
    yield dut.validate_sink.invalidate.eq(0)

    yield dut.sink.header_only.eq(0)

def pop_packet(dut, packet=None):
    yield dut.source.ready.eq(1)

    # Get to the first cycle where a read happens
    while not ((yield dut.source.valid) and (yield dut.source.ready)):
        yield

    param = (yield dut.source.parameter)
    if packet:
        packet.param = param

    if not (yield dut.source.header_only):
        i = 0
        while True:
            data = (yield dut.source.data)
            if (yield dut.source.valid) and (yield dut.source.ready):
                if packet:
                    packet.data.append(data)
                if (yield dut.source.last):
                    break
                i += 1
            yield
        packet.length = i + 1

    yield dut.source.ready.eq(0)

    yield


def push_packets(dut, packets, valid_map=None, discarding=True, validate_delay=1, delay=0):
    valid_map = valid_map if valid_map else [True] * len(packets)
    assert len(valid_map) == len(packets)

    for i, packet in enumerate(packets):
        print(i)
        yield from push_packet(dut, packet, valid_map[i], validate_delay)
        while (yield dut.full):
            if discarding:
                yield from push_packet(dut, packet, valid_map[i], validate_delay)
            else:
                yield

        if i != len(packets) - 1:
            for _ in range(delay):
                yield

def pop_packets(dut, packets, n=1, delay=0):
    c = 0
    while c < n:
        packets.append(Packet([]))
        yield from pop_packet(dut, packets[-1])
        # print(f"Received packet {packets[-1].data} {packets[-1].param}")
        if c != n - 1:
            for _ in range(delay):
                yield
        c += 1

class BaseCase(unittest.TestCase):
    def setUp(self):
        print('\n', unittest.TestCase.id(self))

class TestWaitPipe(BaseCase):
    dw = 8
    def test_wait_pipe(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
        dut = WaitPipe(layout, 3, PMTU, self.dw)

        valid_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(PMTU - (9 - j) * 5)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, valid_map), pop_packets(dut, rcv_packets, sum(valid_map, 0))], vcd_name="test.vcd")
        sent_packets = [packet for (packet, valid) in zip(send_packets, valid_map) if valid]
        for s, r in zip(sent_packets, rcv_packets):
            self.assertTrue(s.cmp(r))

    def test_wait_pipe_full(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
        dut = WaitPipe(layout, 3, PMTU, dw=self.dw)

        valid_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(PMTU - (9 - j) * 5)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, valid_map, discarding=True, delay=0), pop_packets(dut, rcv_packets, sum(valid_map, 0), delay=PMTU * 5)], vcd_name="test.vcd")
        sent_packets = [packet for (packet, valid) in zip(send_packets, valid_map) if valid]
        for s, r in zip(sent_packets, rcv_packets):
            self.assertTrue(s.cmp(r))

    def test_wait_pipe_full_non_discarding(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
        dut = WaitPipe(layout, 3, PMTU, dw=self.dw)

        valid_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(PMTU - (9 - j) * 5)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, valid_map, discarding=False, delay=0), pop_packets(dut, rcv_packets, sum(valid_map, 0), delay=PMTU * 5)], vcd_name="test.vcd")
        sent_packets = [packet for (packet, valid) in zip(send_packets, valid_map) if valid]
        for s, r in zip(sent_packets, rcv_packets):
            self.assertTrue(s.cmp(r))

    def test_wait_pipe_header_only(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
        dut = WaitPipe(layout, 8, PMTU, self.dw, buffered_in=False)

        header_only_map = [True, False, True, True, False, False, True, False, False]
        send_packets = [Packet([(j * i) % PMTU for i in range(0 if header_only_map[j - 1] else 100)], (j * 7) % 3) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets), pop_packets(dut, rcv_packets, len(send_packets))], vcd_name="test.vcd")
        for i, (s, r) in enumerate(zip(send_packets, rcv_packets)):
            if header_only_map[i]:
                self.assertTrue(s.cmp_headers(r))
            else:
                self.assertTrue(s.cmp(r))

    def test_wait_pipe_header_only_non_discard(self):
        layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
        dut = WaitPipe(layout, 3, PMTU, buffered_in=False, discarding=False, dw=self.dw)

        send_packets = [Packet([], j) for j in range(1, 10)]
        rcv_packets = []

        run_simulation(dut, [push_packets(dut, send_packets, discarding=False), pop_packets(dut, rcv_packets, len(send_packets), delay=10)], vcd_name="test.vcd")
        for s, r in zip(send_packets, rcv_packets):
            self.assertTrue(s.cmp_headers(r))

   # TODO: This test requires for validate to depend combinatorially on ready (which seems to be impossible to do with the Migen simulator)
   # This is due to the fact that validate_delay is 0 here and thus validate should be asserted for one cycle on last when ready is asserted.
   # def test_wait_pipe_header_mixed_non_discard(self):
   #     layout = EndpointDescription([("data", self.dw)], [("parameter", 10), ("length", 32)])
   #     dut = WaitPipe(layout, 3, PMTU, buffered_in=False, discarding=False, dw=self.dw)

   #     header_only_map = [True, True, True, True, False, True, False, True, True]
   #     send_packets = [Packet([(j * i) % PMTU for i in range(0 if header_only_map[j - 1] else 100)], j) for j in range(1, 10)]
   #     rcv_packets = []

   #     run_simulation(dut, [push_packets(dut, send_packets, discarding=False, validate_delay=0), pop_packets(dut, rcv_packets, len(send_packets), delay=10)], vcd_name="test.vcd")
   #     for i, (s, r) in enumerate(zip(send_packets, rcv_packets)):
   #         print(f"{s.param} {s.length} {s.data}")
   #         print(f"{r.param} {r.length} {r.data}")
   #         if header_only_map[i]:
   #             self.assertTrue(s.cmp_headers(r))
   #         else:
   #             self.assertTrue(s.cmp(r))

if __name__ == "__main__":
    unittest.main()
