#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest
import zlib

from migen import *

from liteeth.common import eth_phy_description
from liteeth.mac.crc import LiteEthMACCRC32Inserter, LiteEthMACCRC32Checker
from liteeth.frontend.stream import LiteEthStream2UDPTX, LiteEthUDP2StreamRX

# Helpers ------------------------------------------------------------------------------------------

def transfer(test, dut, packets, dw, errors=None):
    lanes    = dw//8
    received = []
    masks    = []
    end_errors = []

    def producer():
        prng = random.Random(17)
        for n, packet in enumerate(packets):
            for offset in range(0, len(packet), lanes):
                for _ in range(prng.randrange(3)):
                    yield
                word = packet[offset:offset + lanes]
                last = offset + lanes >= len(packet)
                yield dut.sink.data.eq(int.from_bytes(word, "little"))
                yield dut.sink.last.eq(last)
                yield dut.sink.be.eq((1 << len(word)) - 1)
                if hasattr(dut.sink, "error"):
                    error = 0
                    if errors is not None:
                        for i in range(lanes):
                            if offset + i in errors[n]:
                                error |= 1 << i
                    yield dut.sink.error.eq(error)
                if hasattr(dut.sink, "dst_port"):
                    yield dut.sink.dst_port.eq(1234)
                yield dut.sink.valid.eq(1)
                yield
                while not (yield dut.sink.ready):
                    yield
                yield dut.sink.valid.eq(0)

    @passive
    def monitor():
        prng    = random.Random(23)
        current = bytearray()
        stalled = None
        while True:
            yield dut.source.ready.eq(prng.randrange(2))
            yield
            valid = (yield dut.source.valid)
            ready = (yield dut.source.ready)
            data  = (yield dut.source.data)
            last  = (yield dut.source.last)
            mask = (yield dut.source.be)
            error = (yield dut.source.error) if hasattr(dut.source, "error") else 0
            # Compare qualified data: unused lanes carry no payload.
            qualified = data & sum(0xff << (8*i) for i in range(lanes) if mask & (1 << i))
            item = (qualified, mask, last, error & mask)
            if stalled is not None:
                test.assertTrue(valid)
                test.assertEqual(item, stalled)
            stalled = item if valid and not ready else None
            if valid and ready:
                test.assertNotEqual(mask, 0)
                test.assertEqual(mask & (mask + 1), 0)
                if not last:
                    test.assertEqual(mask, (1 << lanes) - 1)
                for i in range(lanes):
                    if mask & (1 << i):
                        current.append((data >> (8*i)) & 0xff)
                masks.append(mask)
                if last:
                    received.append(bytes(current))
                    end_errors.append(bool(error & mask))
                    current.clear()

    def timeout():
        for _ in range(10000):
            if len(received) == len(packets):
                return
            yield
        test.fail(f"Only received {len(received)}/{len(packets)} packets")

    # Bound both producer and consumer waits even when a packet is lost.
    run_simulation(dut, [passive(producer)(), monitor(), timeout()])
    return received, end_errors

# Tests --------------------------------------------------------------------------------------------

class TestByteEnable(unittest.TestCase):
    def test_arp_byte_masks_and_length(self):
        from liteeth.core.arp import LiteEthARPTX
        mac = 0x102030405060
        ip  = 0xc0a80101
        target = 0xc0a80102
        expected = (bytes.fromhex("0001080006040001") + mac.to_bytes(6, "big") +
            ip.to_bytes(4, "big") + b"\xff"*6 + target.to_bytes(4, "big") + bytes(18))
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                dut = LiteEthARPTX(mac, ip, dw)
                received = bytearray()
                def check():
                    yield dut.sink.valid.eq(1)
                    yield dut.sink.request.eq(1)
                    yield dut.sink.ip_address.eq(target)
                    yield dut.source.ready.eq(1)
                    for _ in range(128):
                        yield
                        if (yield dut.source.valid):
                            mask = (yield dut.source.be)
                            self.assertNotEqual(mask, 0)
                            self.assertEqual(mask & (mask + 1), 0)
                            if not (yield dut.source.last):
                                self.assertEqual(mask, (1 << (dw//8)) - 1)
                            data = (yield dut.source.data)
                            received.extend((data >> (8*i)) & 0xff for i in range(dw//8) if mask & (1 << i))
                            if (yield dut.source.last):
                                return
                    self.fail("ARP packet did not complete")
                run_simulation(dut, check())
                self.assertEqual(received, expected)

    def test_etherbone_bus_selection_is_not_stream_mask(self):
        from liteeth.frontend.etherbone import LiteEthEtherboneWishboneSlave, LiteEthEtherboneWishboneMaster
        for cls in [LiteEthEtherboneWishboneSlave, LiteEthEtherboneWishboneMaster]:
            with self.subTest(cls=cls.__name__):
                dut = cls()
                def check():
                    if cls == LiteEthEtherboneWishboneSlave:
                        yield dut.bus.sel.eq(5)
                        yield dut.bus.we.eq(1)
                        yield dut.bus.stb.eq(1)
                        yield dut.bus.cyc.eq(1)
                        for _ in range(8):
                            yield
                            if (yield dut.source.valid):
                                self.assertEqual((yield dut.source.be), 0xf)
                                self.assertEqual((yield dut.source.byte_enable), 5)
                                return
                    else:
                        yield dut.sink.be.eq(0xf)
                        yield dut.sink.byte_enable.eq(5)
                        yield dut.sink.we.eq(1)
                        yield dut.sink.valid.eq(1)
                        yield dut.sink.last.eq(1)
                        for _ in range(8):
                            yield
                            if (yield dut.bus.stb):
                                self.assertEqual((yield dut.bus.sel), 5)
                                return
                    self.fail("No bus request")
                run_simulation(dut, check())

    def test_crc_byte_oracle(self):
        for dw in [8, 32, 64]:
            packets = [bytes((i*17 + n) % 256 for i in range(n)) for n in range(60, 77)]
            frames = [p + zlib.crc32(p).to_bytes(4, "little") for p in packets]
            with self.subTest(dw=dw, direction="insert"):
                dut = LiteEthMACCRC32Inserter(eth_phy_description(dw))
                received, _ = transfer(self, dut, packets, dw)
                self.assertEqual(received, frames)
            with self.subTest(dw=dw, direction="check"):
                dut = LiteEthMACCRC32Checker(eth_phy_description(dw))
                received, errors = transfer(self, dut, frames, dw)
                self.assertEqual(received, packets)
                self.assertEqual(errors, [False]*len(packets))

    def test_crc_error_qualification_and_recovery(self):
        for dw in [8, 32, 64]:
            payload = bytes(range(61))
            frame   = payload + zlib.crc32(payload).to_bytes(4, "little")
            # Payload, first FCS byte, last FCS byte, invalid lane, then clean recovery.
            frames = [frame]*5
            errors = [{3}, {len(payload)}, {len(frame) - 1}, {len(frame)}, set()]
            with self.subTest(dw=dw):
                dut = LiteEthMACCRC32Checker(eth_phy_description(dw))
                received, got_errors = transfer(self, dut, frames, dw, errors)
                self.assertEqual(received, [payload]*5)
                self.assertEqual(got_errors, [True, True, True, False, False])

    def test_streamer_boundaries(self):
        for dw in [8, 32, 64]:
            packets = [bytes(range(n)) for n in range(1, 18)]
            with self.subTest(dw=dw, direction="tx"):
                dut = LiteEthStream2UDPTX(udp_port=1234, data_width=dw, fifo_depth=32, with_be=True)
                received, _ = transfer(self, dut, packets, dw)
                self.assertEqual(received, packets)
            with self.subTest(dw=dw, direction="rx"):
                dut = LiteEthUDP2StreamRX(udp_port=1234, data_width=dw, fifo_depth=8, with_be=True)
                received, _ = transfer(self, dut, packets, dw)
                self.assertEqual(received, packets)

class TestByteEnableLayouts(unittest.TestCase):
    def test_final_word_masks(self):
        from liteeth.common import eth_packet_last_mask
        for width in [8, 16, 32, 64]:
            lanes = width//8
            dut = Module()
            length = Signal(8)
            mask = Signal(lanes)
            dut.comb += mask.eq(eth_packet_last_mask(width, length))
            def check():
                for n in range(1, 3*lanes + 1):
                    yield length.eq(n)
                    yield
                    used = (n - 1) % lanes + 1
                    self.assertEqual((yield mask), sum(1 << i for i in range(used)))
            run_simulation(dut, check())

    def test_protocol_payloads_are_independent(self):
        from liteeth.common import eth_udp_user_description, eth_ipv4_user_description
        udp = eth_udp_user_description(32)
        ip = eth_ipv4_user_description(32)
        self.assertEqual(udp.payload_layout, [("data", 32), ("be", 4), ("error", 4)])
        self.assertEqual(udp.payload_layout, ip.payload_layout)
        udp.payload_layout.append(("extra", 1))
        self.assertEqual(len(ip.payload_layout), 3)
        self.assertEqual(len(eth_udp_user_description(32).payload_layout), 3)
