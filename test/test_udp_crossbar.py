#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from liteeth.common import *
from liteeth.core.udp import LiteEthUDPCrossbar

from test.test_stream import StreamPacket, stream_inserter

# Test UDP Crossbar --------------------------------------------------------------------------------

class TestUDPCrossbar(unittest.TestCase):
    def run_port(self, crossbar_dw, port_dw, lengths, tx_buffer_depth=None, seed=42):
        """Send packets on a narrow user port, collect them on the crossbar master (always ready)
        and return (received packets as bytes, number of mid-packet bubbles)."""
        prng     = random.Random(seed)
        packets  = []
        for length in lengths:
            data = [prng.randrange(256) for _ in range(length)]
            packets.append(StreamPacket(data, {"dst_port": 0x1234}))
        crossbar = LiteEthUDPCrossbar(crossbar_dw)
        port     = crossbar.get_port(0x1234, dw=port_dw, tx_buffer_depth=tx_buffer_depth)
        recvd, bubbles = [], [0]

        def collector():
            source = crossbar.master.source
            yield source.ready.eq(1)
            current, in_packet = [], False
            while len(recvd) < len(packets):
                yield
                if (yield source.valid):
                    data    = (yield source.data)
                    last    = (yield source.last)
                    last_be = (yield source.last_be)
                    nbytes  = last_be.bit_length() if (last and last_be) else crossbar_dw//8
                    current += list(data.to_bytes(crossbar_dw//8, "little")[:nbytes])
                    in_packet = not last
                    if last:
                        recvd.append(current)
                        current = []
                elif in_packet:
                    bubbles[0] += 1

        run_simulation(crossbar, [
            stream_inserter(port.sink, src=packets, valid_rand=0),
            collector(),
        ])
        return [p.data for p in packets], recvd, bubbles[0]

    def test_narrow_port_back_to_back(self):
        # 32-bit port on a 64-bit crossbar (ex Etherbone on a 10G core): with a TX buffer, packets
        # are sent back-to-back (no mid-packet bubble) and intact.
        lengths = [4, 12, 36, 40, 44, 80, 12]
        sent, recvd, bubbles = self.run_port(64, 32, lengths, tx_buffer_depth=16)
        self.assertEqual(recvd, sent)
        self.assertEqual(bubbles, 0)

    def test_narrow_port_without_buffer_has_bubbles(self):
        # Without the buffer, the up-converter provides a 64-bit word every other cycle.
        sent, recvd, bubbles = self.run_port(64, 32, [40, 80])
        self.assertEqual(recvd, sent)
        self.assertGreater(bubbles, 0)

    def test_etherbone_tx_buffer(self):
        # Etherbone (32-bit port) requests a TX buffer holding its largest reply on wide cores only.
        from liteeth.core import LiteEthUDPIPCore
        from liteeth.frontend.etherbone import LiteEthEtherbone
        from liteeth.phy.model import LiteEthPHYModel
        for dw, buffer_depth, expected in [(64, 16, 10), (64, 4, 4), (32, 16, None), (8, 4, None)]:
            with self.subTest(dw=dw, buffer_depth=buffer_depth):
                pads = Record([("source_valid", 1), ("source_ready", 1), ("source_data", 8),
                               ("sink_valid", 1), ("sink_ready", 1), ("sink_data", 8)])
                phy  = LiteEthPHYModel(pads)
                core = LiteEthUDPIPCore(phy, 0x10e2d5000000, 0xc0a80132, int(100e6), dw=dw)
                LiteEthEtherbone(core.udp, 1234, buffer_depth=buffer_depth)
                tx_buffer = getattr(core.udp.crossbar, "tx_buffer", None)
                if expected is None:
                    self.assertIsNone(tx_buffer)
                else:
                    # 16 bytes of headers + 4 bytes per word, in 64-bit words.
                    self.assertEqual(tx_buffer.payload_fifo.depth, expected)
