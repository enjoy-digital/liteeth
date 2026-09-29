#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.common import *
from liteeth.core.ip import LiteEthIPTX
from liteeth.core.udp import LiteEthUDPRX
from liteeth.frontend.etherbone import LiteEthEtherboneRecordReceiver
from liteeth.frontend.stream import LiteEthUDP2StreamRX
from liteeth.mac.core import LiteEthMACCore


def packet_beats(data, dw, **params):
    lanes = dw//8
    return [dict(params, data=int.from_bytes(data[n:n + lanes], "little"),
        be=(1 << len(data[n:n + lanes])) - 1, last=int(n + lanes >= len(data)))
        for n in range(0, len(data), lanes)]


def exercise_stream(dut, beats, fields, cycles=1000):
    """Bounded simulation with source gaps, output stalls and stability checks."""
    received = []
    def bench():
        sent    = 0
        pending = False
        stalled = None
        for cycle in range(cycles):
            if not pending and sent < len(beats) and cycle % 5 != 0:
                pending = True
            yield dut.sink.valid.eq(pending)
            if pending:
                for name, value in beats[sent].items():
                    yield getattr(dut.sink, name).eq(value)
            yield dut.source.ready.eq(cycle % 7 not in (0, 1, 2))
            yield
            valid = (yield dut.source.valid)
            values = {}
            for field in fields:
                values[field] = (yield getattr(dut.source, field))
            if stalled is not None:
                assert valid and values == stalled, (stalled, values)
            stalled = values if valid and not (yield dut.source.ready) else None
            if valid and (yield dut.source.ready):
                received.append(values)
            if pending and (yield dut.sink.ready):
                sent += 1
                pending = False
        assert sent == len(beats), (sent, len(beats))
    run_simulation(dut, bench())
    return received


class TestPacketBoundaries(unittest.TestCase):
    def test_etherbone_mixed_record(self):
        dut = LiteEthEtherboneRecordReceiver(buffer_depth=8)
        words = [0x100, 0xdeadbeef, 0x800, 0x400]
        beats = packet_beats(b"".join(w.to_bytes(4, "little") for w in words), 32,
            wcount=1, rcount=1, byte_enable=15)
        got = exercise_stream(dut, beats*2, ["we", "addr", "data", "base_addr", "last"])
        self.assertEqual(len(got), 4)
        for write, read in zip(got[::2], got[1::2]):
            self.assertEqual((write["we"], write["addr"], write["data"]), (1, 0x40, 0xdeadbeef))
            self.assertEqual((read["we"], read["addr"], read["base_addr"]), (0, 0x100, 0x800))

    def test_udp_lengths_and_recovery(self):
        for dw in [8, 16, 32, 64]:
            with self.subTest(dw=dw):
                beats = []
                for length, ip_length in [(7, 16), (8, 16), (17, 16), (9, 9)]:
                    header = bytes.fromhex("12345678") + length.to_bytes(2, "big") + bytes(2)
                    beats += packet_beats(header + bytes([0xaa])*8, dw,
                        length=ip_length, protocol=17, ip_address=0x12345678)
                got = exercise_stream(LiteEthUDPRX(0, dw), beats, ["data", "be", "last", "length"])
                self.assertEqual(len(got), 1)
                self.assertEqual((got[0]["be"], got[0]["last"], got[0]["length"]), (1, 1, 1))

    def test_udp_truncated_final_word(self):
        for dw in [8, 16, 32, 64]:
            for declared in [5, 11]:
                with self.subTest(dw=dw, declared=declared):
                    header = bytes.fromhex("12345678") + (8 + declared).to_bytes(2, "big") + bytes(2)
                    beats = packet_beats(header + bytes([0xaa])*3, dw,
                        length=8 + declared, protocol=17)
                    got = exercise_stream(LiteEthUDPRX(0, dw), beats, ["be", "last", "error"])
                    self.assertEqual(sum(b["be"].bit_count() for b in got), 3)
                    self.assertEqual(got[-1]["last"], 1)
                    self.assertEqual(got[-1]["error"], got[-1]["be"])

    def test_streamer_error_lanes(self):
        for dw in [8, 32, 64]:
            for depth in [None, 4]:
                with self.subTest(dw=dw, depth=depth):
                    beats = []
                    expected = []
                    for lane in range(dw//8):
                        for be in [(1 << (dw//8)) - 1, 1]:
                            beats.append(dict(data=0x12, be=be, error=1 << lane,
                                last=1, dst_port=1234))
                            expected.append(int(bool(be & (1 << lane))))
                    dut = LiteEthUDP2StreamRX(udp_port=1234, data_width=dw,
                        fifo_depth=depth, with_be=True)
                    got = exercise_stream(dut, beats, ["error", "be", "last"])
                    self.assertEqual([b["error"] for b in got], expected)

    def test_ip_checksum_after_arp_failure(self):
        for dw in [8, 32, 64]:
            for with_buffer in [False, True]:
                with self.subTest(dw=dw, with_buffer=with_buffer):
                    class ARP:
                        request  = stream.Endpoint(arp_table_request_layout)
                        response = stream.Endpoint(arp_table_response_layout)
                    arp = ARP()
                    dut = LiteEthIPTX(0x112233445566, 0xc0a80101, arp, dw=dw, with_buffer=with_buffer)
                    frames = []
                    def bench():
                        sent = reply = requests = 0
                        frame = bytearray()
                        yield arp.request.ready.eq(1)
                        for cycle in range(500):
                            yield dut.source.ready.eq(cycle % 5 > 1)
                            yield dut.sink.valid.eq(sent < 3)
                            yield dut.sink.last.eq(1)
                            yield dut.sink.be.eq(1)
                            yield dut.sink.data.eq(0x12)
                            yield dut.sink.length.eq(1)
                            yield dut.sink.protocol.eq(17)
                            yield dut.sink.ip_address.eq(0xc0a80102 + sent)
                            yield arp.response.valid.eq(reply != 0)
                            yield arp.response.failed.eq(reply == 1)
                            yield arp.response.mac_address.eq(0x010203040506)
                            yield
                            if (yield arp.request.valid):
                                requests += 1
                                reply = requests
                            if (yield arp.response.valid) and (yield arp.response.ready):
                                reply = 0
                            if sent < 3 and (yield dut.sink.ready):
                                sent += 1
                            if (yield dut.source.valid) and (yield dut.source.ready):
                                data = (yield dut.source.data)
                                be = (yield dut.source.be)
                                frame.extend((data >> (8*i)) & 255 for i in range(dw//8) if be & (1 << i))
                                if (yield dut.source.last):
                                    frames.append(bytes(frame))
                                    frame.clear()
                        self.assertEqual(sent, 3)
                    run_simulation(dut, bench())
                    self.assertEqual(len(frames), 2)
                    for n, frame in enumerate(frames):
                        total = sum(int.from_bytes(frame[i:i + 2], "big") for i in range(0, 20, 2))
                        while total >> 16:
                            total = (total & 65535) + (total >> 16)
                        self.assertEqual(total, 65535)
                        self.assertEqual(int.from_bytes(frame[16:20], "big"), 0xc0a80103 + n)
                        self.assertEqual(frame[20:], b"\x12")

    def test_mac_gap_after_packet_fifo(self):
        for sys_datapath in [False, True]:
            with self.subTest(sys_datapath=sys_datapath):
                class PHY:
                    dw = 8
                    sink   = stream.Endpoint(eth_phy_description(8))
                    source = stream.Endpoint(eth_phy_description(8))
                phy = PHY()
                dut = LiteEthMACCore(phy, 8, with_sys_datapath=sys_datapath,
                    with_store_and_forward=True)
                seen = []
                def send():
                    sent = 0
                    for cycle in range(900):
                        yield dut.sink.valid.eq(sent < 128)
                        yield dut.sink.data.eq(sent & 255)
                        yield dut.sink.be.eq(1)
                        yield dut.sink.last.eq(sent in [63, 127])
                        yield
                        if sent < 128 and (yield dut.sink.ready):
                            sent += 1
                    self.assertEqual(sent, 128)
                @passive
                def observe():
                    for cycle in range(1000):
                        yield phy.sink.ready.eq(cycle >= 300)
                        yield
                        if (yield phy.sink.valid) and (yield phy.sink.ready):
                            seen.append((cycle, (yield phy.sink.last)))
                run_simulation(dut, {"sys": send(), "eth_tx": observe()},
                    clocks={"sys": 10, "eth_tx": 12, "eth_rx": 12})
                ends = [i for i, (_, last) in enumerate(seen) if last]
                self.assertEqual(len(ends), 2)
                self.assertGreaterEqual(seen[ends[0] + 1][0] - seen[ends[0]][0] - 1, 12)
                for start, end in [(0, ends[0]), (ends[0] + 1, ends[1])]:
                    self.assertEqual(seen[end][0] - seen[start][0], end - start)
