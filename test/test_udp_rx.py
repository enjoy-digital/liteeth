#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from liteeth.common import *
from liteeth.core.udp import LiteEthUDPRX

from test.test_stream import StreamPacket, stream_inserter, stream_collector

# Test UDP RX --------------------------------------------------------------------------------------

class TestUDPRX(unittest.TestCase):
    def run_rx(self, dw, lengths, pads, seed=42):
        """Feed IPv4 payloads (UDP header + data + Ethernet padding) and check the UDP payloads."""
        prng    = random.Random(seed)
        packets = []
        payloads = []
        for length, pad in zip(lengths, pads):
            data   = [prng.randrange(256) for _ in range(length)]
            header = list((0x1234).to_bytes(2, "big") + (0x5678).to_bytes(2, "big") +
                          (udp_header.length + length).to_bytes(2, "big") + bytes(2))
            # IP length excludes the Ethernet padding (the IP layer forwards the padded payload).
            packets.append(StreamPacket(header + data + [0xaa]*pad, {
                "length"     : udp_header.length + length,
                "protocol"   : udp_protocol,
                "ip_address" : 0x12345678,
            }))
            payloads.append(data)
        dut   = LiteEthUDPRX(ip_address=0x12345678, dw=dw)
        recvd = []
        run_simulation(dut, [
            stream_inserter(dut.sink, src=packets, seed=seed),
            stream_collector(dut.source, dest=recvd, expect_npackets=len(packets), seed=seed),
        ])
        self.assertEqual(len(recvd), len(packets))
        for n, (sent, got) in enumerate(zip(payloads, recvd)):
            msg = f"dw={dw} length={lengths[n]} pad={pads[n]}"
            self.assertEqual(got.data, sent, msg)
            self.assertEqual(got.params["length"], lengths[n], msg)

    def test_padded_minimum_frames(self):
        # Minimum Ethernet frame: 60 bytes = 14 (MAC) + 20 (IPv4) + 26 bytes of IPv4 payload, so
        # UDP payloads < 18 bytes are received with padding. When the padding shares the last data
        # word, the IP length (not the padded frame's last_be) must end the packet (regression:
        # a 17-byte payload was received as 18 bytes on 16/32/64-bit data paths).
        for dw in [8, 16, 32, 64]:
            lengths = list(range(1, 25))
            pads    = [max(0, 26 - (udp_header.length + l)) for l in lengths]
            with self.subTest(dw=dw):
                self.run_rx(dw, lengths, pads)

    def test_extra_padding(self):
        # Trailing bytes beyond the IP length (padding added by other stacks) are dropped.
        prng = random.Random(3)
        for dw in [32, 64]:
            lengths = [prng.randrange(1, 64) for _ in range(24)]
            pads    = [prng.randrange(0, 20) for _ in range(24)]
            with self.subTest(dw=dw):
                self.run_rx(dw, lengths, pads, seed=dw)
