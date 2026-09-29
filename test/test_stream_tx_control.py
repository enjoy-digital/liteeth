#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.frontend.stream import LiteEthStream2UDPTX


class TestStreamTXControl(unittest.TestCase):
    def test_enable_destination_and_stalls(self):
        for dw in [8, 32, 64]:
            for depth in [None, 4]:
                for with_be in [False, True]:
                    for with_csr in [False, True]:
                        with self.subTest(dw=dw, depth=depth, with_be=with_be, with_csr=with_csr):
                            dut = LiteEthStream2UDPTX(ip_address=1, udp_port=100, data_width=dw,
                                fifo_depth=depth, with_be=with_be, with_csr=with_csr)
                            received = []
                            def bench():
                                sent = 0
                                stalled = None
                                for cycle in range(90):
                                    enabled = 6 <= cycle < 10 or cycle >= 30
                                    ip = 1 if cycle < 10 else 2
                                    port = 100 if cycle < 10 else 200
                                    if with_csr:
                                        yield dut._enable.storage.eq(enabled)
                                        yield dut._ip_address.storage.eq(ip)
                                        yield dut._udp_port.storage.eq(port)
                                    else:
                                        yield dut.enable.eq(enabled)
                                        yield dut.ip_address.eq(ip)
                                        yield dut.udp_port.eq(port)
                                    yield dut.sink.valid.eq(sent < 4)
                                    yield dut.sink.data.eq(0x10 + sent)
                                    yield dut.sink.last.eq(sent in [2, 3])
                                    if with_be:
                                        yield dut.sink.be.eq(1 if sent in [2, 3] else (1 << (dw//8)) - 1)
                                    yield dut.source.ready.eq(cycle >= 15 and cycle % 4 != 0)
                                    yield
                                    valid = (yield dut.source.valid)
                                    fields = []
                                    for name in ["data", "be", "last", "length", "ip_address", "src_port", "dst_port"]:
                                        fields.append((yield getattr(dut.source, name)))
                                    if cycle < 6:
                                        self.assertFalse(valid)
                                        if depth is None:
                                            self.assertEqual((yield dut.sink.ready), 0)
                                    if stalled is not None:
                                        self.assertTrue(valid)
                                        self.assertEqual(fields, stalled)
                                    stalled = fields if valid and not (yield dut.source.ready) else None
                                    if valid and (yield dut.source.ready):
                                        received.append(fields)
                                    if sent < 4 and (yield dut.sink.ready):
                                        sent += 1
                                self.assertEqual(sent, 4)
                            run_simulation(dut, bench())
                            self.assertEqual(len(received), 4)
                            for n, beat in enumerate(received):
                                data, be, last, length, ip, src, dst = beat
                                first_packet = n < (3 if depth is not None else 1)
                                expected_bytes = 1 if with_be and n in [2, 3] else dw//8
                                expected_length = (2*dw//8 + (1 if with_be else dw//8)) if depth is not None and n < 3 else expected_bytes
                                self.assertEqual(data, 0x10 + n)
                                self.assertEqual(be, (1 << expected_bytes) - 1)
                                self.assertEqual(last, int(depth is None or n in [2, 3]))
                                self.assertEqual(length, expected_length)
                                self.assertEqual((ip, src, dst), (1, 100, 100) if first_packet else (2, 200, 200))
