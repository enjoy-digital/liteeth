#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import struct
import unittest

from liteeth.common import *
from liteeth.core.ip import LiteEthIPRX
from test.test_packet_boundaries import packet_beats, exercise_stream


def wire_packet(length, target=0xc0a80132, payload=b"test", version_ihl=0x45, corrupt=False):
    header = bytearray(struct.pack(">BBHHHBBHII",
        version_ihl, 0, length, 0, 0, 64, 17, 0, 0xc0a80101, target))
    checksum = sum(struct.unpack(">10H", header))
    while checksum >> 16:
        checksum = (checksum & 65535) + (checksum >> 16)
    header[10:12] = ((checksum ^ 65535) ^ int(corrupt)).to_bytes(2, "big")
    return bytes(header) + payload


class TestIPRXValidation(unittest.TestCase):
    def test_invalid_length_and_recovery(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                beats = []
                for length in [0, 1, 19, 20, 24]:
                    beats += packet_beats(wire_packet(length), dw)
                got = exercise_stream(LiteEthIPRX(0, 0xc0a80132, dw=dw), beats,
                    ["data", "be", "last", "length"], cycles=1500)
                self.assertEqual(sum(b["last"] for b in got), 1)
                self.assertTrue(all(b["length"] == 4 for b in got))
                payload = []
                for beat in got:
                    payload += [(beat["data"] >> (8*n)) & 255 for n in range(dw//8) if beat["be"] & (1 << n)]
                self.assertEqual(bytes(payload), b"test")

    def test_destination_policy(self):
        addresses = [0xc0a80132, 0xc0a80199, 0xffffffff, 0xe0000181, 0xc0a801ff]
        for permissive in [False, True]:
            for dw in [8, 32, 64]:
                with self.subTest(dw=dw, with_broadcast=permissive):
                    beats = []
                    for address in addresses:
                        beats += packet_beats(wire_packet(24, target=address), dw)
                    dut = LiteEthIPRX(0, addresses[0], with_broadcast=permissive, dw=dw)
                    got = exercise_stream(dut, beats, ["be", "last", "length"], cycles=1500)
                    self.assertEqual(sum(b["last"] for b in got), len(addresses) if permissive else 1)

    def test_invalid_header_and_recovery(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                packets = [wire_packet(24, version_ihl=0x65), wire_packet(24, version_ihl=0x46),
                    wire_packet(24, corrupt=True), wire_packet(24)[:12], wire_packet(24)]
                beats = []
                for packet in packets:
                    beats += packet_beats(packet, dw)
                got = exercise_stream(LiteEthIPRX(0, 0xc0a80132, dw=dw), beats,
                    ["be", "last", "length"], cycles=1500)
                self.assertEqual(sum(b["last"] for b in got), 1)
                self.assertTrue(all(b["length"] == 4 for b in got))
