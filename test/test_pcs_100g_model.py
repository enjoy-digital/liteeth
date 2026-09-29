#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest

from test.model.pcs_100g import (AM_INTERVAL, LANES, Scrambler, marker, marker_lane,
    bip8, distribute, multiplex, demultiplex, recover)


class TestPCS100GModel(unittest.TestCase):
    def blocks(self, count):
        prng = random.Random(42)
        return [(prng.getrandbits(64) << 2) | 2 for _ in range(count)]

    def test_marker_known_vector(self):
        # IEEE 802.3-2012 82.2.8's transmitted-bit example, lane 0, BIP3=0x0f.
        expected = "10 10000011 00010110 10000100 11110000 01111100 11101001 01111011 00001111"
        actual = "".join(str((marker(0, 15) >> bit) & 1) for bit in range(66))
        self.assertEqual(actual, expected.replace(" ", ""))
        for lane in range(LANES):
            self.assertEqual(marker_lane(marker(lane, 0xa5)), lane)
            self.assertIsNone(marker_lane(marker(lane) ^ (1 << 2)))

    def test_bip_bit_assignments(self):
        # Independent one-hot checks of every position in Table 82-4.
        groups = [
            [2, 10, 18, 26, 34, 42, 50, 58], [3, 11, 19, 27, 35, 43, 51, 59],
            [4, 12, 20, 28, 36, 44, 52, 60], [0, 5, 13, 21, 29, 37, 45, 53, 61],
            [1, 6, 14, 22, 30, 38, 46, 54, 62], [7, 15, 23, 31, 39, 47, 55, 63],
            [8, 16, 24, 32, 40, 48, 56, 64], [9, 17, 25, 33, 41, 49, 57, 65],
        ]
        for parity, bits in enumerate(groups):
            for bit in bits:
                self.assertEqual(bip8(1 << bit), 1 << parity)

    def test_scrambler_and_self_synchronization(self):
        blocks = self.blocks(100)
        tx, rx = Scrambler(), Scrambler(descramble=True, state=0)
        scrambled = [tx.block(block) for block in blocks]
        recovered = [rx.block(block) for block in scrambled]
        self.assertEqual(recovered[1:], blocks[1:])
        self.assertTrue(all(block & 3 == 2 for block in scrambled))
        # The marker stage must not advance the common scrambler's state.
        lanes = distribute(scrambled, interval=5)
        self.assertEqual([lanes[lane][0] for lane in range(20)], scrambled[:20])

    def test_real_marker_spacing(self):
        blocks = [(n << 2) | 2 for n in range(2*AM_INTERVAL*LANES)]
        lanes = distribute(blocks)
        for lane in range(LANES):
            self.assertEqual(lanes[lane][0], blocks[lane])
            self.assertEqual(marker_lane(lanes[lane][AM_INTERVAL]), lane)
            self.assertEqual(marker_lane(lanes[lane][2*AM_INTERVAL + 1]), lane)
        recovered, errors = recover(lanes)
        self.assertEqual(recovered, blocks[AM_INTERVAL*LANES:])
        self.assertEqual(errors, 0)

    def test_pma_bit_order(self):
        # Single set bit identifies its physical lane, mux phase and position independently.
        for lane in range(LANES):
            for bit in [0, 1, 2, 33, 65]:
                logical = [[0] for _ in range(LANES)]
                logical[lane][0] = 1 << bit
                physical = multiplex(logical)
                self.assertEqual(sum(map(sum, physical)), 1)
                self.assertEqual(physical[lane % 4][5*bit + lane//4], 1)
                self.assertEqual(demultiplex(physical), logical)

    def test_lane_reorder_skew_and_errors(self):
        interval = 31
        blocks = self.blocks(5*interval*LANES)
        lanes = distribute(blocks, interval)
        prng = random.Random(12)
        order = list(range(LANES))
        prng.shuffle(order)
        delayed = [[None]*(lane % 7) + lanes[lane] for lane in order]
        data, errors = recover(delayed, interval, max_skew=6)
        self.assertEqual(data, blocks[interval*LANES:])
        self.assertEqual(errors, 0)
        self.assertEqual(recover(delayed, interval, max_skew=5)[0], [])
        # Corrupt one payload bit: BIP reports it, alignment and data delivery continue.
        lanes[3][interval + 8] ^= 1 << 29
        data, errors = recover(lanes, interval, max_skew=0)
        self.assertEqual(errors, 1)
        self.assertEqual(len(data), 4*interval*LANES)
        # Lose the second marker on one lane: reacquire from markers three and four.
        lanes[3][2*interval + 1] ^= 1 << 2
        data, _ = recover(lanes, interval, max_skew=0)
        self.assertEqual(data, blocks[3*interval*LANES:])
        # Duplicate a lane identity: never release ambiguous data.
        lanes[3] = lanes[4]
        self.assertEqual(recover(lanes, interval, max_skew=0)[0], [])

    def test_incomplete_and_missing_lanes(self):
        lanes = distribute(self.blocks(3*31*LANES), interval=31)
        lanes[5] = []
        self.assertEqual(recover(lanes, interval=31, max_skew=0), ([], 0))
        with self.assertRaises(ValueError):
            multiplex(lanes)
        with self.assertRaises(ValueError):
            distribute([2])

    def test_deleted_block_reacquisition(self):
        blocks = self.blocks(4*31*LANES)
        lanes = distribute(blocks, interval=31)
        del lanes[7][40]
        recovered, errors = recover(lanes, interval=31, max_skew=1)
        self.assertEqual(recovered, blocks[2*31*LANES:])
        self.assertEqual(errors, 0)

    def test_marker_parity_copies(self):
        blocks = self.blocks(2*31*LANES)
        for bit in [26, 58]:
            lanes = distribute(blocks, interval=31)
            lanes[0][-1] ^= 1 << bit
            recovered, errors = recover(lanes, interval=31, max_skew=0)
            self.assertEqual(recovered, blocks[31*LANES:])
            self.assertEqual(errors, 1)
