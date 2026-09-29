#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.mac.packet import LiteEthMACPacketWriter, LiteEthMACPacketReader
from liteeth.mac.sram import LiteEthMACSRAMWriter, LiteEthMACSRAMReader
from liteeth.mac.wishbone import LiteEthMACWishboneInterface
from test.test_packet_boundaries import packet_beats


class TestMACBounds(unittest.TestCase):
    def test_writer_capacity_and_recovery(self):
        for dw in [8, 16, 32, 64]:
            for depth in [1, 3, 4]:
                for mtu in [depth*dw//8, depth*dw//8 + 32]:
                    with self.subTest(dw=dw, depth=depth, mtu=mtu):
                        capacity = depth*dw//8
                        dut = LiteEthMACPacketWriter(dw, depth, eth_mtu=mtu)
                        lengths = [capacity, capacity + 1, 8*capacity + 1, max(1, capacity - 1)]
                        results = []
                        writes = []
                        def bench():
                            cycle = 0
                            for length in lengths:
                                beats = packet_beats(bytes(n & 255 for n in range(length)), dw)
                                sent = 0
                                addresses = []
                                for _ in range(8*len(beats) + 32):
                                    yield dut.sink.valid.eq(sent < len(beats))
                                    if sent < len(beats):
                                        for name, value in beats[sent].items():
                                            yield getattr(dut.sink, name).eq(value)
                                    yield dut.source.ready.eq(cycle % 4 > 1)
                                    cycle += 1
                                    yield
                                    if (yield dut.source.valid):
                                        self.assertLess((yield dut.offset), capacity)
                                    if (yield dut.source.valid) and (yield dut.source.ready):
                                        addresses.append((yield dut.offset))
                                    if sent < len(beats) and (yield dut.sink.ready):
                                        sent += 1
                                    if (yield dut.done) or (yield dut.drop):
                                        results.append(((yield dut.done), (yield dut.drop), (yield dut.error)))
                                        break
                                else:
                                    self.fail("Writer did not finish the packet")
                                self.assertEqual(sent, len(beats))
                                writes.append(addresses)
                                yield
                        run_simulation(dut, bench())
                        self.assertEqual(results, [(1, 0, 0), (0, 1, 1), (0, 1, 1), (1, 0, 0)])
                        self.assertEqual(writes[0], list(range(0, capacity, dw//8)))
                        self.assertEqual(writes[3], list(range(0, lengths[3], dw//8)))

    def test_reader_invalid_length_and_recovery(self):
        for dw in [8, 16, 32, 64]:
            dut = LiteEthMACPacketReader(dw, depth=3)
            def bench():
                yield dut.source.ready.eq(1)
                yield dut.sink.valid.eq(1)
                yield dut.sink.last.eq(1)
                for length in [0, 3*dw//8 + 1, 1]:
                    yield dut.length.eq(length)
                    yield dut.enable.eq(1)
                    yield
                    yield dut.enable.eq(0)
                    transfers = 0
                    for _ in range(20):
                        yield
                        if (yield dut.source.valid) and (yield dut.source.ready):
                            transfers += 1
                        if (yield dut.done):
                            break
                    else:
                        self.fail("Reader did not retire command")
                    self.assertEqual(transfers, int(length == 1))
                    yield
            with self.subTest(dw=dw):
                run_simulation(dut, bench())

    def test_rx_slots_wrap(self):
        for nslots in [1, 2, 3, 4, 5]:
            with self.subTest(nslots=nslots):
                dut = LiteEthMACSRAMWriter(32, 3, nslots=nslots, endianness="little", timestamp=Signal(32, reset=77))
                dut.specials += dut.mems
                def bench():
                    for n in range(2*nslots + 1):
                        yield dut.sink.valid.eq(1)
                        yield dut.sink.data.eq(0x100 + n)
                        yield dut.sink.be.eq(15)
                        yield dut.sink.last.eq(1)
                        yield
                        self.assertEqual((yield dut.sink.ready), 1)
                        yield dut.sink.valid.eq(0)
                        for _ in range(20):
                            yield
                            if (yield dut.ev.available.pending):
                                break
                        else:
                            self.fail("Missing RX completion")
                        slot = (yield dut._slot.status)
                        self.assertEqual(slot, n % nslots)
                        self.assertEqual((yield dut._length.status), 4)
                        self.assertEqual((yield dut._timestamp.status), 77)
                        self.assertEqual((yield dut.mems[slot][0]), 0x100 + n)
                        yield dut.ev.pending.wr_data.eq(1)
                        yield dut.ev.pending.wr_stb.eq(1)
                        yield
                        yield dut.ev.pending.wr_stb.eq(0)
                        yield
                run_simulation(dut, bench())

    def test_tx_commands_and_timestamp_backpressure(self):
        for nslots in [1, 3, 4]:
            for with_timestamp in [False, True]:
                with self.subTest(nslots=nslots, timestamp=with_timestamp):
                    timestamp = Signal(32, reset=123) if with_timestamp else None
                    dut = LiteEthMACSRAMReader(32, 3, nslots=nslots, endianness="little", timestamp=timestamp)
                    dut.specials += dut.mems
                    def bench():
                        for n, mem in enumerate(dut.mems):
                            yield mem[0].eq(0x12340000 + n)
                        yield dut.source.ready.eq(1)
                        for slot, length in [(0, 0), (0, 13), (nslots, 4), (nslots - 1, 3)]:
                            # A power-of-two slot count has no representable invalid slot except 1.
                            if slot >= (1 << len(dut._slot.storage)):
                                continue
                            valid = length == 3
                            yield dut._slot.storage.eq(slot)
                            yield dut._length.storage.eq(length)
                            yield dut._start.wr_stb.eq(1)
                            yield
                            yield dut._start.wr_stb.eq(0)
                            transfers = []
                            for _ in range(30):
                                yield
                                if (yield dut.source.valid):
                                    transfers.append(((yield dut.source.data), (yield dut.source.be), (yield dut.source.last)))
                                if (yield dut.ev.done.pending):
                                    break
                            else:
                                self.fail("Missing TX completion")
                            expected = [(0x12340000 + slot, 7, 1)] if valid else []
                            self.assertEqual(transfers, expected)
                            if with_timestamp:
                                self.assertEqual((yield dut._timestamp.status), 123 if valid else 0)
                            yield dut.ev.pending.wr_data.eq(1)
                            yield dut.ev.pending.wr_stb.eq(1)
                            yield
                            yield dut.ev.pending.wr_stb.eq(0)
                            yield
                    run_simulation(dut, bench())

    def test_full_timestamp_fifo_keeps_completions(self):
        dut = LiteEthMACSRAMReader(32, 4, nslots=1, timestamp=Signal(32, reset=123))
        dut.specials += dut.mems
        def bench():
            yield dut.source.ready.eq(1)
            # Fill the single status slot, then queue a second completion while software stalls.
            for length in [4, 0]:
                yield dut._length.storage.eq(length)
                yield dut._start.wr_stb.eq(1)
                yield
                yield dut._start.wr_stb.eq(0)
                for _ in range(20):
                    yield
            self.assertEqual((yield dut._level.status), 1)
            self.assertEqual((yield dut._timestamp.status), 123)
            yield dut.ev.pending.wr_data.eq(1)
            yield dut.ev.pending.wr_stb.eq(1)
            yield
            yield dut.ev.pending.wr_stb.eq(0)
            for _ in range(10):
                yield
            self.assertEqual((yield dut._level.status), 0)
            self.assertEqual((yield dut.ev.done.pending), 1)
            self.assertEqual((yield dut._timestamp.status), 0)
        run_simulation(dut, bench())

    def test_non_power_of_two_wishbone_slots(self):
        dut = LiteEthMACWishboneInterface(32, nrxslots=3, ntxslots=3, eth_mtu=12,
            rxslots_read_only=False, endianness="little")
        def bench():
            for slot in range(3):
                bus = dut.bus_rx
                yield bus.adr.eq(4*slot)
                yield bus.dat_w.eq(0x100 + slot)
                yield bus.sel.eq(15)
                yield bus.we.eq(1)
                yield bus.cyc.eq(1)
                yield bus.stb.eq(1)
                for _ in range(20):
                    yield
                    if (yield bus.ack):
                        break
                else:
                    self.fail("Wishbone write timed out")
                yield bus.cyc.eq(0)
                yield bus.stb.eq(0)
                yield
                self.assertEqual((yield dut.sram.writer.mems[slot][0]), 0x100 + slot)
        run_simulation(dut, bench())
