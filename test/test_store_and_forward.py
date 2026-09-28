#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

import random
import inspect
import unittest

from types import SimpleNamespace

from migen import *

from litex.soc.interconnect import stream

from liteeth.common import eth_mtu_default, eth_needs_store_and_forward, eth_packet_fifo_depth
from liteeth.core import LiteEthUDPIPCore
from liteeth.core.udp import LiteEthUDP
from liteeth.fifo import PacketDropFIFO
from liteeth.mac.core import LiteEthMACCore

from test.model import phy

# Test PacketDropFIFO ------------------------------------------------------------------------------

DW = 32

drop_fifo_description = stream.EndpointDescription(
    payload_layout = [("data", DW)],
    param_layout   = [("tag",  16)],
)

class TestPacketDropFIFO(unittest.TestCase):
    def run_fifo(self, packets, depth, reader_gap=0, writer_gap=0, seed=42):
        """Push packets, as (tag, [words]), through a PacketDropFIFO and collect what comes out."""
        dut   = PacketDropFIFO(drop_fifo_description, payload_depth=depth)
        got   = []
        drops = []
        # Enough cycles for every packet plus the stalls the reader inserts.
        cycles = sum(len(w) for _, w in packets) * (reader_gap + writer_gap + 2) + 1000

        def writer():
            prng = random.Random(seed)
            for tag, words in packets:
                for i, word in enumerate(words):
                    for _ in range(prng.randint(0, writer_gap)):
                        yield dut.sink.valid.eq(0)
                        yield
                    yield dut.sink.valid.eq(1)
                    yield dut.sink.first.eq(int(i == 0))
                    yield dut.sink.last.eq(int(i == len(words) - 1))
                    yield dut.sink.data.eq(word)
                    yield dut.sink.tag.eq(tag)
                    yield
                    # Never backpressures: that is the whole point of this FIFO.
                    self.assertEqual((yield dut.sink.ready), 1)
            yield dut.sink.valid.eq(0)

        def reader():
            prng    = random.Random(seed + 1)
            current = None
            for _ in range(cycles):
                yield dut.source.ready.eq(int(prng.randint(0, reader_gap) == 0))
                yield
                if (yield dut.source.valid) and (yield dut.source.ready):
                    if (yield dut.source.first):
                        self.assertIsNone(current, "first beat inside a packet")
                        current = ((yield dut.source.tag), [])
                    self.assertIsNotNone(current, "beat outside a packet")
                    current[1].append((yield dut.source.data))
                    if (yield dut.source.last):
                        got.append(current)
                        current = None
            self.assertIsNone(current, "packet left unterminated")

        def drop_watcher():
            for _ in range(cycles):
                if (yield dut.drop):
                    drops.append(1)
                yield

        run_simulation(dut, [writer(), reader(), drop_watcher()])

        # Whatever came out must be a subsequence of what went in: each packet whole, unaltered and
        # in order. And every packet must be accounted for exactly once, delivered or dropped.
        remaining = iter(packets)
        for packet in got:
            self.assertIn(packet, remaining,
                "emitted a packet that was never sent, was altered, or arrived out of order")
        self.assertEqual(len(got) + len(drops), len(packets))
        return got, len(drops)

    @staticmethod
    def gen(prng, count, min_len, max_len):
        return [(tag, [prng.randrange(2**DW) for _ in range(prng.randint(min_len, max_len))])
                for tag in range(count)]

    def test_no_drops_when_reader_keeps_up(self):
        prng = random.Random(1)
        packets = self.gen(prng, 30, 1, 20)
        got, drops = self.run_fifo(packets, depth=256)
        self.assertEqual(drops, 0)
        self.assertEqual(got, packets)

    def test_single_word_packets(self):
        prng = random.Random(2)
        packets = self.gen(prng, 20, 1, 1)
        got, drops = self.run_fifo(packets, depth=16)
        self.assertEqual(drops, 0)
        self.assertEqual(got, packets)

    def test_packet_exactly_filling_an_empty_fifo(self):
        prng = random.Random(3)
        packets = [(0, [prng.randrange(2**DW) for _ in range(16)])]
        got, drops = self.run_fifo(packets, depth=16)
        self.assertEqual(drops, 0)
        self.assertEqual(got, packets)

    def test_back_to_back_full_frames_at_the_depth_we_size_for(self):
        # A 1530-byte MTU is 193 words at dw=64 and eth_packet_fifo_depth rounds that to 256. The
        # slack is what lets the next frame start arriving while the current one drains, so
        # back-to-back full frames must survive a reader running at the writer's rate.
        self.assertEqual(eth_packet_fifo_depth(eth_mtu_default, 64), 256)
        prng = random.Random(4)
        packets = self.gen(prng, 8, 193, 193)
        got, drops = self.run_fifo(packets, depth=256)
        self.assertEqual(drops, 0)
        self.assertEqual(got, packets)

    def test_packet_larger_than_fifo_is_dropped_not_truncated(self):
        prng = random.Random(5)
        packets = self.gen(prng, 4, 24, 24)
        got, drops = self.run_fifo(packets, depth=16)
        self.assertEqual(got, [])
        self.assertEqual(drops, 4)

    def test_slow_reader_drops_whole_packets_only(self):
        prng = random.Random(6)
        packets = self.gen(prng, 40, 1, 20)
        got, drops = self.run_fifo(packets, depth=32, reader_gap=8)
        # run_fifo already asserts that survivors are whole and in order.
        self.assertGreater(drops, 0)
        self.assertGreater(len(got), 0)

    def test_recovers_after_a_burst_of_drops(self):
        prng = random.Random(7)
        packets = self.gen(prng, 40, 8, 8)
        got, drops = self.run_fifo(packets, depth=32, reader_gap=20)
        self.assertGreater(drops, 0)
        self.assertGreater(len(got), 0)

    def test_bursty_writer(self):
        prng = random.Random(8)
        packets = self.gen(prng, 40, 1, 12)
        self.run_fifo(packets, depth=32, reader_gap=4, writer_gap=3)

# Test Packet FIFO Depth ---------------------------------------------------------------------------

class TestPacketFIFODepth(unittest.TestCase):
    def test_depth_holds_a_whole_frame(self):
        for eth_mtu in [eth_mtu_default, 9022]:
            for dw in [8, 16, 32, 64]:
                depth = eth_packet_fifo_depth(eth_mtu, dw)
                words = -(-(eth_mtu + 8 + 4)//(dw//8)) # Frame + Preamble + FCS, rounded up.
                self.assertEqual(depth & (depth - 1), 0, "depth must be a power of two")
                self.assertGreater(depth, words, "depth must leave room beyond one whole frame")

# Test Store-and-Forward Selection -----------------------------------------------------------------

class TestStoreAndForwardAuto(unittest.TestCase):
    def test_throughput_from_width_and_clock(self):
        # XGMII sets its 64-bit width per instance; its class attribute says 8.
        self.assertTrue(eth_needs_store_and_forward(SimpleNamespace(dw=64, tx_clk_freq=156.25e6)))
        self.assertTrue(eth_needs_store_and_forward(SimpleNamespace(dw=8,  tx_clk_freq=312.5e6)))
        self.assertFalse(eth_needs_store_and_forward(SimpleNamespace(dw=8, tx_clk_freq=125e6)))
        # No transmit clock at all, like the simulation models.
        self.assertFalse(eth_needs_store_and_forward(SimpleNamespace(dw=8)))

    FAST, SLOW = 312.5e6, 125e6
    SETTINGS   = [
        (FAST, "auto", True),
        (SLOW, "auto", False),
        (SLOW, True,   True),
        (FAST, False,  False),
    ]

    @staticmethod
    def model_phy(tx_clk_freq):
        model = phy.PHY(8, debug=False)
        model.tx_clk_freq = tx_clk_freq
        return model

    @classmethod
    def contains(cls, module, kind):
        if isinstance(module, kind):
            return True
        return any(cls.contains(m, kind) for _, m in getattr(module, "_submodules", []))

    def test_mac_core(self):
        for tx_clk_freq, setting, expected in self.SETTINGS:
            core = LiteEthMACCore(phy=self.model_phy(tx_clk_freq), dw=8,
                with_store_and_forward=setting)
            self.assertEqual(core.with_store_and_forward, expected, (tx_clk_freq, setting))
            self.assertEqual(self.contains(core, PacketDropFIFO), expected, (tx_clk_freq, setting))

    def test_udp_ip_core(self):
        for tx_clk_freq, setting, expected in self.SETTINGS:
            core = LiteEthUDPIPCore(phy=self.model_phy(tx_clk_freq), mac_address=0x10e2d5000000,
                ip_address="192.168.1.50", clk_freq=int(100e6), with_store_and_forward=setting)
            self.assertEqual(core.mac.core.with_store_and_forward, expected, (tx_clk_freq, setting))
            self.assertEqual(core.udp.crossbar.with_store_and_forward, expected,
                (tx_clk_freq, setting))

    def test_udp_alone_defaults_to_no_fifos(self):
        # Standalone, LiteEthUDP cannot see the MAC, so it keeps upstream behaviour unless told.
        params = inspect.signature(LiteEthUDP.__init__).parameters
        self.assertIs(params["with_store_and_forward"].default, False)
        self.assertNotIn("phy", params)

    def test_invalid_setting(self):
        with self.assertRaises(AssertionError):
            LiteEthMACCore(phy=self.model_phy(self.FAST), dw=8, with_store_and_forward="yes")
