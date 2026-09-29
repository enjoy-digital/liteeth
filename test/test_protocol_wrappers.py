#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import shutil
import unittest

from liteeth.core.udp import LiteEthUDPTX
from liteeth.core.icmp import LiteEthICMPTX
from liteeth.frontend.etherbone import LiteEthEtherbonePacketTX
from test.test_packet_boundaries import packet_beats, exercise_stream
from test import test_packet_contracts_rtl as rtl


class TestProtocolWrappers(unittest.TestCase):
    def check_packets(self, dut, dw, protocol):
        beats = []
        expected = []
        for n, length in enumerate([1, 2, 7, 8, 9, 16, 17]):
            data = bytes(range(length))
            params = dict(length=length, ip_address=0xc0a80100 + n)
            if protocol == "udp":
                params.update(src_port=1000 + n, dst_port=2000 + n)
                header = (1000 + n).to_bytes(2, "big") + (2000 + n).to_bytes(2, "big")
                header += (8 + length).to_bytes(2, "big") + bytes(2)
            elif protocol == "icmp":
                params.update(msgtype=0, code=0, checksum=0x1234 + n, quench=0x12345678 + n)
                header = bytes(2) + (0x1234 + n).to_bytes(2, "big") + (0x12345678 + n).to_bytes(4, "big")
            else:
                params.update(src_port=2000 + n, pf=0, pr=0, nr=0)
                header = bytes.fromhex("4e6f104400000000")
            beats += packet_beats(data, dw, **params)
            expected.append((header + data, length + 8, 0xc0a80100 + n))
        fields = ["data", "be", "last", "length", "ip_address"]
        fields += ["src_port", "dst_port"] if protocol == "etherbone" else ["protocol"]
        got = exercise_stream(dut, beats, fields, cycles=2000)
        packets = []
        data = bytearray()
        params = None
        for beat in got:
            current = {name: beat[name] for name in fields if name not in ["data", "be", "last"]}
            if params is None:
                params = current
            self.assertEqual(current, params)
            data.extend((beat["data"] >> (8*i)) & 255 for i in range(dw//8) if beat["be"] & (1 << i))
            if beat["last"]:
                packets.append((bytes(data), params["length"], params["ip_address"]))
                if protocol == "etherbone":
                    self.assertEqual(params["src_port"], 1234)
                    self.assertEqual(params["dst_port"], 1999 + len(packets))
                else:
                    self.assertEqual(params["protocol"], 17 if protocol == "udp" else 1)
                data.clear()
                params = None
        self.assertFalse(data)
        self.assertEqual(packets, expected)

    def test_udp_back_to_back(self):
        for dw in [8, 16, 32, 64]:
            with self.subTest(dw=dw):
                self.check_packets(LiteEthUDPTX(0, dw), dw, "udp")

    def test_icmp_back_to_back(self):
        for dw in [8, 16, 32, 64]:
            with self.subTest(dw=dw):
                self.check_packets(LiteEthICMPTX(0, dw), dw, "icmp")

    def test_etherbone_back_to_back(self):
        self.check_packets(LiteEthEtherbonePacketTX(1234), 32, "etherbone")


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog required")
class TestProtocolWrappersRTL(unittest.TestCase):
    check_rtl = rtl.TestPacketContractsRTL.check_rtl

    def test_udp_tx_packets(self):
        beats = []
        expected = []
        for n in range(3):
            beats += packet_beats(bytes([n]), 32, length=1, ip_address=0xc0a80101 + n,
                src_port=1234, dst_port=2345)
            wire = bytes.fromhex("04d2092900090000") + bytes([n])
            expected += packet_beats(wire, 32, length=9, ip_address=0xc0a80101 + n)
        self.check_rtl(LiteEthUDPTX(0, 32), beats, expected)
