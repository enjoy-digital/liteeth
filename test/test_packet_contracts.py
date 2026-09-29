#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *
from litex.gen import LiteXModule

from liteeth.common import *
from liteeth.fifo import PacketDropFIFO
from liteeth.core.icmp import LiteEthICMPRX, LiteEthICMPEcho
from liteeth.core.dhcp import LiteEthDHCPRX
from liteeth.frontend.etherbone import LiteEthEtherbone, LiteEthEtherboneRecordReceiver
from liteeth.frontend.stream import LiteEthUDP2StreamRX
from litex.soc.interconnect import wishbone
from test.test_etherbone import UDP, etherbone

from test.test_packet_boundaries import packet_beats, exercise_stream
from test.test_dhcp import build_dhcp_response, mac_address, transaction_id


class TestPacketContracts(unittest.TestCase):
    def test_etherbone_rejects_before_bus_access(self):
        for depth in [3, 8]:
            for fault in ["short", "long", "oversized", "partial", "error", "flags", "empty"]:
                with self.subTest(depth=depth, fault=fault):
                    dut = LiteEthEtherboneRecordReceiver(buffer_depth=depth)
                    words = [0x100, 0x1234]
                    count = 1
                    if fault == "short":
                        count = 2
                    if fault == "long":
                        words += [0x5678]
                    if fault == "oversized":
                        words += [0x5678]*16
                        count = 17
                    if fault == "empty":
                        count = 0
                    bad = packet_beats(b"".join(w.to_bytes(4, "little") for w in words), 32,
                        wcount=count, rcount=0, byte_enable=15, error=0, wff=0)
                    if fault == "partial":
                        bad[-1]["be"] = 1
                    if fault == "error":
                        bad[-1]["error"] = 8
                    if fault == "flags":
                        for beat in bad:
                            beat["wff"] = 1
                    good = packet_beats(bytes.fromhex("00010000efbeadde"), 32,
                        wcount=1, rcount=0, byte_enable=15, error=0, wff=0)
                    got = exercise_stream(dut, bad + good, ["we", "addr", "data", "last"])
                    self.assertEqual(got, [dict(we=1, addr=64, data=0xdeadbeef, last=1)])

    def test_icmp_echo_rejects_bad_packets(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                dut = LiteEthICMPEcho(dw, fifo_depth=16)
                beats = []
                for actual, declared, error in [(3, 9, 0), (40, 8, 0), (4, 4, 1), (4, 4, 0)]:
                    beats += packet_beats(bytes(range(actual)), dw, length=declared, error=error)
                got = exercise_stream(dut, beats, ["data", "be", "last", "length"])
                self.assertEqual(sum(bin(b["be"]).count("1") for b in got), 4)
                self.assertEqual(got[-1]["last"], 1)
                self.assertTrue(all(b["length"] == 4 for b in got))

    def test_icmp_padding_and_truncation(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                header = bytes.fromhex("0800123400010002")
                beats = packet_beats(header + bytes(range(16)), dw, protocol=1, length=11)
                beats += packet_beats(header + bytes(range(3)), dw, protocol=1, length=19)
                got = exercise_stream(LiteEthICMPRX(0, dw), beats, ["be", "last", "error", "length"])
                self.assertEqual(sum(bin(b["be"]).count("1") for b in got), 6)
                self.assertEqual(sum(b["last"] for b in got), 2)
                self.assertEqual(got[-1]["error"], got[-1]["be"])

    def test_discard_at_any_position(self):
        for position in range(4):
            with self.subTest(position=position):
                class DUT(LiteXModule):
                    def __init__(self):
                        layout = stream.EndpointDescription([("data", 32), ("be", 4), ("error", 4)])
                        self.fifo = fifo = PacketDropFIFO(layout, 8)
                        self.sink, self.source = fifo.sink, fifo.source
                        self.comb += fifo.discard.eq(fifo.sink.error != 0)
                bad = packet_beats(bytes(range(16)), 32, error=0)
                bad[position]["error"] = 1
                good = packet_beats(b"abcd", 32, error=0)
                got = exercise_stream(DUT(), bad + good, ["data", "last"])
                self.assertEqual(got, [dict(data=int.from_bytes(b"abcd", "little"), last=1)])

    def test_dhcp_truncation_and_next_packet(self):
        good = bytes(build_dhcp_response())
        # Every fixed field, then each byte of the message-type option, plus an errored final word.
        for length in list(range(4, 241, 4)) + [241, 242, 243, len(good)]:
            with self.subTest(length=length):
                class DUT(LiteXModule):
                    def __init__(self):
                        self.port = type("Port", (), {})()
                        self.port.source = stream.Endpoint(eth_udp_user_description(32))
                        self.rx = LiteEthDHCPRX(self.port)
                dut = DUT()
                bad = packet_beats(good[:length], 32, length=len(good), src_port=67, dst_port=68, error=0)
                if length == len(good):
                    bad[-1]["error"] = 8
                beats = bad + packet_beats(good, 32, length=len(good), src_port=67, dst_port=68, error=0)
                results = []
                def bench():
                    yield dut.rx.mac_address.eq(mac_address)
                    yield dut.rx.transaction_id.eq(transaction_id)
                    sent = 0
                    for cycle in range(700):
                        yield dut.port.source.valid.eq(sent < len(beats))
                        if sent < len(beats):
                            for name, value in beats[sent].items():
                                yield getattr(dut.port.source, name).eq(value)
                        yield dut.rx.ack.eq(1)
                        yield
                        if sent < len(beats) and (yield dut.port.source.ready):
                            sent += 1
                        if (yield dut.rx.present):
                            results.append((yield dut.rx.error))
                    self.assertEqual(sent, len(beats))
                run_simulation(dut, bench())
                self.assertEqual(results, [1, 0])

    def test_etherbone_reply_peer_under_backpressure(self):
        class DUT(LiteXModule):
            def __init__(self):
                udp = UDP(32)
                self.etherbone = LiteEthEtherbone(udp, 1234, buffer_depth=8)
                self.memory = wishbone.SRAM(1024)
                self.bus = wishbone.InterconnectPointToPoint(self.etherbone.wishbone.bus, self.memory.bus)
                self.sink, self.source = udp.crossbar.port.source, udp.crossbar.port.sink
        beats = []
        for peer in range(4):
            record = etherbone.EtherboneRecord()
            record.reads = etherbone.EtherboneReads(base_ret_addr=0x100 + 4*peer, addrs=[0])
            packet = etherbone.EtherbonePacket()
            packet.records = [record]
            packet.encode()
            beats += packet_beats(packet.bytes, 32, length=len(packet.bytes),
                src_port=2000 + peer, dst_port=1234, ip_address=0xc0a80101 + peer)
        got = exercise_stream(DUT(), beats, ["data", "be", "last", "ip_address", "dst_port"], cycles=1500)
        first = True
        peers = []
        for beat in got:
            if first:
                peers.append((beat["ip_address"], beat["dst_port"]))
            self.assertEqual((beat["ip_address"], beat["dst_port"]), peers[-1])
            first = bool(beat["last"])
        self.assertEqual(peers, [(0xc0a80101 + n, 2000 + n) for n in range(4)])
        self.assertTrue(first)

    def test_streamer_filter_is_packet_scoped(self):
        for depth in [None, 4]:
            with self.subTest(depth=depth):
                dut = LiteEthUDP2StreamRX(udp_port=1234, data_width=32, fifo_depth=depth, with_be=True)
                got = []
                def bench():
                    sent = 0
                    for cycle in range(50):
                        # Change configuration while the first packet is stalled/buffered.
                        yield dut.udp_port.eq(1234 if cycle < 2 else 4321)
                        yield dut.source.ready.eq(cycle >= 8)
                        yield dut.sink.valid.eq(sent < 4)
                        yield dut.sink.data.eq(sent)
                        yield dut.sink.be.eq(15)
                        yield dut.sink.dst_port.eq(1234)
                        yield dut.sink.last.eq(sent in [2, 3])
                        yield
                        if sent < 4 and (yield dut.sink.ready):
                            sent += 1
                        if (yield dut.source.valid) and (yield dut.source.ready):
                            got.append((yield dut.source.data))
                    self.assertEqual(sent, 4)
                run_simulation(dut, bench())
                self.assertEqual(got, [0, 1, 2])
