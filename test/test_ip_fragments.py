#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random
import struct

from migen import *

from liteeth.common import *
from liteeth.core.ip import LiteEthIPRX

from test.test_stream import StreamPacket, stream_inserter, stream_collector

# Helpers ------------------------------------------------------------------------------------------

def ipv4_packet(payload, flags_offset=0, sender_ip=0xc0a80135, target_ip=0xc0a80132, protocol=udp_protocol):
    header = bytearray(struct.pack(">BBHHHBBHII",
        0x45, 0, ipv4_header_length + len(payload), 0x1234, flags_offset, 64, protocol, 0,
        sender_ip, target_ip))
    s = sum(struct.unpack(">10H", header))
    s = (s & 0xffff) + (s >> 16)
    s = (s & 0xffff) + (s >> 16)
    header[10:12] = struct.pack(">H", ~s & 0xffff)
    return list(header) + list(payload)

# Test IP Fragments --------------------------------------------------------------------------------

class TestIPFragments(unittest.TestCase):
    def test_fragments_dropped(self):
        # IP reassembly is not supported: fragments (MF set or non-zero fragment offset) are dropped,
        # unfragmented packets (with or without DF) are received.
        MF, DF = 0x2000, 0x4000
        cases = [
            (0,        True),  # Unfragmented.
            (DF,       True),  # Unfragmented, Don't Fragment.
            (MF,       False), # First fragment.
            (MF | 185, False), # Middle fragment.
            (185,      False), # Last fragment.
            (0,        True),
        ]
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                prng    = random.Random(dw)
                packets = []
                for flags_offset, _ in cases:
                    payload = [prng.randrange(256) for _ in range(prng.randrange(30, 80))]
                    packets.append(StreamPacket(ipv4_packet(payload, flags_offset), {
                        "ethernet_type" : ethernet_type_ip,
                    }))
                expected = [p.data[ipv4_header_length:] for p, (_, ok) in zip(packets, cases) if ok]
                dut   = LiteEthIPRX(mac_address=0x10e2d5000001, ip_address=0xc0a80132, dw=dw)
                recvd = []

                @passive
                def collector():
                    # Always ready: collect every received packet (so dropped fragments show up as
                    # extra packets instead of stalling the inserter).
                    yield from stream_collector(dut.source, dest=recvd, ready_rand=0)

                def generator():
                    yield from stream_inserter(dut.sink, src=packets)
                    for _ in range(256):
                        yield

                run_simulation(dut, [generator(), collector()])
                self.assertEqual([r.data for r in recvd], expected)
