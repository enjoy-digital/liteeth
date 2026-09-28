#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from liteeth.common import *
from liteeth.mac.padding import LiteEthMACPaddingInserter

from test.test_stream import StreamPacket, stream_inserter, stream_collector

# Test MAC Padding Inserter ------------------------------------------------------------------------

class TestMACPaddingInserter(unittest.TestCase):
    padding = eth_min_frame_length - eth_fcs_length # 60 bytes.

    def run_inserter(self, dw, lengths, seed=42):
        prng    = random.Random(seed)
        packets = [StreamPacket([prng.randrange(256) for _ in range(l)]) for l in lengths]
        dut     = LiteEthMACPaddingInserter(dw, self.padding)
        recvd   = []
        run_simulation(dut, [
            stream_inserter(dut.sink, src=packets, seed=seed),
            stream_collector(dut.source, dest=recvd, expect_npackets=len(packets), seed=seed),
        ])
        self.assertEqual(len(recvd), len(packets))
        for sent, got in zip(packets, recvd):
            msg      = f"dw={dw} lengths={lengths} length={len(sent.data)}"
            # Frames are padded to the minimum length (padding content is unspecified: the rest of
            # the last data word, then zeros), longer frames are unchanged.
            self.assertEqual(got.data[:len(sent.data)], sent.data, msg)
            self.assertEqual(len(got.data), max(len(sent.data), self.padding), msg)

    def test_lengths(self):
        for dw in [8, 16, 32, 64]:
            for length in [1, 7, 42, 55, 56, 57, 58, 59, 60, 61, 64, 65, 100]:
                with self.subTest(dw=dw, length=length):
                    self.run_inserter(dw, [length])

    def test_short_frame_after_near_minimum_frame(self):
        # A frame of 57..59 bytes ends in the last padding word with a smaller last_be: the next
        # short frame must still be padded (regression: counter not reset, next frame sent as a
        # runt).
        for dw in [16, 32, 64]:
            for length in [57, 58, 59]:
                for following in [1, 42, 46, 59]:
                    with self.subTest(dw=dw, length=length, following=following):
                        self.run_inserter(dw, [length, following, following, length, 42])

    def test_random_sequences(self):
        prng = random.Random(7)
        for dw in [8, 32, 64]:
            lengths = [prng.choice([prng.randrange(1, 70), prng.randrange(55, 61)])
                for _ in range(40)]
            with self.subTest(dw=dw):
                self.run_inserter(dw, lengths, seed=dw)
