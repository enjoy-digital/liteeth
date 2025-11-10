#
# This file is part of LiteEth.
#
# Copyright (c) 2025 David Sawatzke <d-git@sawatzke.dev>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from liteeth.common import *
from liteeth.mac.crc import *

from litex.gen.sim import *

from .test_stream import *

# Layout -------------------------------------------------------------------------------------------

def get_stream_desc(dw):
    return [
        ("data",    dw),
        ("last_be", dw // 8),
        ("error",   dw // 8),
    ]

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw):
        self.inserter = LiteEthMACCRC32Inserter(eth_phy_description(dw))
        self.checker  = LiteEthMACCRC32Checker(eth_phy_description(dw))
        self.comb += self.inserter.source.connect(self.checker.sink)

# Test CRC -----------------------------------------------------------------------------------------

class TestCRC(unittest.TestCase):
    def crc_inserter_checker_test(self, dw=32, seed=42, npackets=2, debug_print=False):
        prng = random.Random(seed + 5)

        dut  = DUT(dw)
        desc = get_stream_desc(dw)
        full_last_be = (1 << (dw // 8)) - 1

        packets = []

        for n in range(npackets):
            header = {}
            datas = [prng.randrange(2**8) for _ in range(prng.randrange(dw - 1) + 1)]
            packets.append(StreamPacket(datas, header))

        recvd_packets = []
        run_simulation(
            dut,
            [
                stream_inserter(
                    dut.inserter.sink,
                    src         = packets,
                    seed        = seed,
                    debug_print = debug_print,
                    valid_rand  = 50,
                ),
                stream_collector(
                    dut.checker.source,
                    dest            = recvd_packets,
                    expect_npackets = npackets,
                    seed            = seed,
                    debug_print     = debug_print,
                    ready_rand      = 50,
                ),
            ],
            vcd_name="crc_test_{}bit_seed{}.vcd".format(dw, seed),
        )

        if not compare_packets(packets, recvd_packets):
            print("crc_test_{}bit_seed{}".format(dw, seed))
            print(len(packets))
            for i in range(len(packets)):
                print(i)
                print(packets[i].data)
                print(recvd_packets[i].data)
            assert False

    def test_8bit_loopback(self):
        for seed in range(42, 48):
            with self.subTest(seed=seed):
                self.crc_inserter_checker_test(dw=8, seed=seed)

    def test_32bit_loopback(self):
        for seed in range(42, 48):
            with self.subTest(seed=seed):
                self.crc_inserter_checker_test(dw=32, seed=seed)

    # TODO the 64 bit case has a few issues unrelated to LiteEthMACCRC32Check
    # def test_64bit_loopback(self):
    #     for seed in range(42, 70):
    #         with self.subTest(seed=seed):
    #             self.crc_inserter_checker_test(dw=64, seed=seed)

    # 16 bit is completely broken
    # def test_16bit_loopback(self):
    #     for seed in range(42, 70):
    #         with self.subTest(seed=seed):
    #             self.crc_inserter_checker_test(dw=16, seed=seed)
