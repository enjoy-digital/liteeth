#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.mac.axis import LiteEthMACAXIStream
from test.test_packet_boundaries import packet_beats, exercise_stream


class TestMACAXIStream(unittest.TestCase):
    def test_rx_errors_masks_and_lengths(self):
        for width in [64, 512]:
            with self.subTest(width=width):
                dut = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(
                    LiteEthMACAXIStream(width, eth_mtu=128, tx_fifo_depth=32, rx_fifo_depth=32))
                # Present an AXI RX endpoint as the driver's sink, preserving the actual TX sink.
                dut.sink = dut.rx
                inputs = []
                expected = bytes(range(60))
                for size, fault in [(60, None), (61, "error"), (59, None), (125, None),
                                    (80, "hole"), (80, "partial"), (60, None)]:
                    words = packet_beats(bytes(range(size)), width, user=0)
                    for word in words:
                        word["keep"] = word.pop("be")
                    if fault == "error":
                        words[-1]["user"] = 1
                    if fault == "hole":
                        words[0]["keep"] &= ~2
                    if fault == "partial":
                        words[0]["keep"] >>= 1
                    inputs += words
                got = exercise_stream(dut, inputs, ["data", "be", "last", "error"], cycles=1000)
                packets, data = [], bytearray()
                for word in got:
                    self.assertEqual(word["error"], 0)
                    data.extend((word["data"] >> (8*i)) & 255 for i in range(width//8) if word["be"] & (1 << i))
                    if word["last"]:
                        packets.append(bytes(data))
                        data.clear()
                self.assertEqual(packets, [expected, expected])

    def test_tx_packet_commit_and_stalls(self):
        dut = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(
            LiteEthMACAXIStream(512, eth_mtu=256, tx_fifo_depth=8, rx_fifo_depth=8))
        dut.source = dut.tx
        inputs = []
        for n, length in enumerate([1, 42, 59, 60, 65, 192]):
            inputs += packet_beats(bytes([n])*length, 512, error=0)
        got = exercise_stream(dut, inputs, ["data", "keep", "last", "user"], cycles=500)
        lengths, count = [], 0
        for word in got:
            self.assertEqual(word["user"], 0)
            count += bin(word["keep"]).count("1")
            if word["last"]:
                lengths.append(count)
                count = 0
        self.assertEqual(lengths, [60, 60, 60, 60, 65, 192])

    def test_tx_errors_survive_padding(self):
        for width in [64, 128, 256, 512]:
            with self.subTest(width=width):
                dut = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(
                    LiteEthMACAXIStream(width, eth_mtu=128, tx_fifo_depth=32, rx_fifo_depth=32))
                dut.source = dut.tx
                inputs = [dict(data=0x123456, be=mask, last=1, error=error)
                    for mask, error in [(7, 0), (7, 1), (5, 0), (0, 0), (7, 0)]]
                got = exercise_stream(dut, inputs, ["keep", "last", "user"], cycles=1000)
                self.assertEqual([word["user"] for word in got if word["last"]], [0, 1, 1, 1, 0])
                self.assertTrue(all(word["user"] == 0 for word in got if not word["last"]))

    def test_rx_overflow_and_recovery(self):
        dut = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(
            LiteEthMACAXIStream(512, eth_mtu=128, tx_fifo_depth=4, rx_fifo_depth=4, param_depth=4))
        def bench():
            yield dut.source.ready.eq(0)
            for n in range(10):
                yield dut.rx.valid.eq(1)
                yield dut.rx.data.eq(n)
                yield dut.rx.keep.eq((1 << 60) - 1)
                yield dut.rx.last.eq(1)
                yield
            yield dut.rx.valid.eq(0)
            yield
            self.assertEqual((yield dut.rx_packets), 10)
            self.assertEqual((yield dut.rx_drops), 6)
            self.assertEqual((yield dut.rx_bad_frames), 0)
            yield dut.source.ready.eq(1)
            received = []
            for _ in range(30):
                yield
                if (yield dut.source.valid):
                    self.assertEqual((yield dut.source.last), 1)
                    received.append((yield dut.source.data))
            self.assertEqual(received, [0, 1, 2, 3])
            yield dut.rx.valid.eq(1)
            yield dut.rx.data.eq(42)
            yield
            yield dut.rx.valid.eq(0)
            for _ in range(20):
                yield
                if (yield dut.source.valid):
                    self.assertEqual((yield dut.source.data), 42)
                    break
            else:
                self.fail("No recovery after overflow")
        run_simulation(dut, bench())

    def test_invalid_configuration(self):
        for options in [dict(dw=32), dict(rx_fifo_depth=3), dict(tx_fifo_depth=2),
                        dict(eth_mtu=63), dict(param_depth=1)]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                LiteEthMACAXIStream(**options)
