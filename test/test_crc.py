#
# This file is part of LiteEth.
#
# Copyright (c) 2025 David Sawatzke <d-git@sawatzke.dev>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random
import zlib

from migen import *

from liteeth.common import *
from liteeth.mac.crc import *

from litex.gen.sim import *

from test.test_stream import mask_be, StreamPacket, stream_inserter, stream_collector, compare_packets

# Layout -------------------------------------------------------------------------------------------

def get_stream_desc(dw):
    return [
        ("data",    dw),
        ("be", dw // 8),
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
        full_be = (1 << (dw // 8)) - 1

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

    def test_receive_error_in_discarded_fcs(self):
        for dw in (8, 32, 64):
            for length in (8, 12):
                for location in (None, "fcs", "unused"):
                    payload = bytes(range(length))
                    frame = payload + zlib.crc32(payload).to_bytes(4, "little")
                    lanes = dw//8
                    if location == "unused" and len(frame) % lanes == 0:
                        continue
                    with self.subTest(dw=dw, length=length, location=location):
                        dut = LiteEthMACCRC32Checker(eth_phy_description(dw))
                        received = []
                        errors = []
                        crc_errors = []

                        def driver():
                            yield dut.source.ready.eq(1)
                            for _ in range(3):
                                yield
                            for offset in range(0, len(frame), lanes):
                                word = frame[offset:offset + lanes]
                                last = offset + lanes >= len(frame)
                                error = 0
                                if last and location == "fcs":
                                    error = 1 << (len(word) - 1)
                                elif last and location == "unused":
                                    error = ((1 << lanes) - 1) ^ ((1 << len(word)) - 1)
                                yield dut.sink.data.eq(int.from_bytes(word, "little"))
                                yield dut.sink.last.eq(last)
                                yield dut.sink.be.eq((1 << len(word)) - 1)
                                yield dut.sink.error.eq(error)
                                yield dut.sink.valid.eq(1)
                                yield
                                while not (yield dut.sink.ready):
                                    yield
                            yield dut.sink.valid.eq(0)
                            for _ in range(12):
                                yield

                        @passive
                        def monitor():
                            while True:
                                if (yield dut.error):
                                    crc_errors.append(1)
                                if (yield dut.source.valid) and (yield dut.source.ready):
                                    last = (yield dut.source.last)
                                    be = (yield dut.source.be)
                                    count = be.bit_length() if last else lanes
                                    value = (yield dut.source.data)
                                    received.extend((value >> (8*i)) & 0xff for i in range(count))
                                    if last:
                                        errors.append(bool((yield dut.source.error) & be))
                                yield

                        run_simulation(dut, [driver(), monitor()])
                        self.assertEqual(received, list(payload))
                        self.assertEqual(errors, [location == "fcs"])
                        self.assertEqual(crc_errors, [])

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
