#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest

from migen import *
from liteeth.core.ip import LiteEthIPV4Checksum
from bench.udp_throughput import measure


class TestWideDatapath(unittest.TestCase):
    def test_checksum(self):
        prng = random.Random(42)
        headers = [bytes([0xff]*20), bytes(20)]
        headers += [bytes(prng.randrange(256) for _ in range(20)) for _ in range(50)]
        for parallel in [False, True]:
            for skip in [False, True]:
                with self.subTest(parallel=parallel, skip=skip):
                    dut = LiteEthIPV4Checksum(skip_checksum=skip, with_parallel=parallel)
                    def check():
                        for header in headers:
                            values = [int.from_bytes(header[i:i + 2], "big")
                                for i in range(0, 20, 2) if not (skip and i == 10)]
                            total = sum(values)
                            while total >> 16:
                                total = (total & 0xffff) + (total >> 16)
                            expected = total ^ 0xffff
                            yield dut.reset.eq(1)
                            yield dut.ce.eq(1)
                            yield
                            yield dut.reset.eq(0)
                            yield dut.header.eq(int.from_bytes(header, "little"))
                            yield dut.ce.eq(0)
                            for _ in range(3):
                                yield
                                self.assertEqual((yield dut.done), 0)
                            yield dut.ce.eq(1)
                            yield
                            for _ in range(16):
                                if (yield dut.done):
                                    break
                                yield
                            else:
                                self.fail("Checksum did not complete")
                            self.assertEqual((yield dut.value), expected)
                            yield dut.header.eq(0)
                            for _ in range(3):
                                yield
                                self.assertEqual((yield dut.done), 1)
                                self.assertEqual((yield dut.value), expected)
                    run_simulation(dut, check())

    def test_udp_headers_and_payload(self):
        for width in [128, 256, 512]:
            lanes = width//8
            for length in sorted({1, lanes - 1, lanes, lanes + 1, 2*lanes + 1}):
                with self.subTest(width=width, length=length):
                    measure(width, length, packets=4, stalls=True)

    def test_packet_rate(self):
        # Unicast packets include a resolved ARP lookup, all Ethernet/IP/UDP header operations,
        # and both checksum directions. Guard the measured improvement without claiming wire rate.
        for width, maximum in [(128, 12), (256, 11), (512, 10)]:
            with self.subTest(width=width):
                result = measure(width, 18, packets=8)
                self.assertLessEqual(result["cycles_per_packet"], maximum)

    def test_changing_lengths(self):
        for width in [128, 256, 512]:
            with self.subTest(width=width):
                measure(width, [1, 65, 18, 129, 3, 256, 33], stalls=True)

    def test_generator(self):
        from test.test_gen import generate_config

        for width in [128, 256, 512]:
            with self.subTest(width=width):
                rtl = generate_config("udp_baser_25g", data_width=width,
                    udp_ports={"udp0": {"data_width": width, "tx_fifo_depth": 512, "rx_fifo_depth": 512}})
                self.assertIn("GTYE4_CHANNEL", rtl)
                self.assertIn(f"[{width-1}:0]", rtl)
                self.assertIn("udp0_sink_keep", rtl)
