#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.common import eth_mtu_default
from liteeth.mac.packet import LiteEthMACPacketWriter, LiteEthMACPacketReader

# Helpers ------------------------------------------------------------------------------------------

def mac_packet_last_be(dw, length):
    return 1 << ((length - 1) % (dw//8))

def mac_packet_words(dw, data):
    words = []
    for offset in range(0, len(data), dw//8):
        word = 0
        for byte, value in enumerate(data[offset:offset + dw//8]):
            word |= value << (8*byte)
        words.append(word)
    return words

def mac_packet_bytes(dw, word, last=0, last_be=0):
    data = []
    for byte in range(dw//8):
        if last and 2**byte > last_be:
            break
        data.append((word >> (8*byte)) & 0xff)
    return data

def mac_packet_test_cases(length=10):
    return [(dw, [n + 1 for n in range(length)]) for dw in [32, 64]]

def wait_until(cond, timeout=128):
    for _ in range(timeout):
        if (yield from cond()):
            return False
        yield
    return True

def mac_packet_send(sink, dw, data, error=0):
    words = mac_packet_words(dw, data)
    for n, word in enumerate(words):
        last = n == len(words) - 1
        yield sink.data.eq(word)
        yield sink.last.eq(last)
        if hasattr(sink, "last_be"):
            yield sink.last_be.eq(mac_packet_last_be(dw, len(data)) if last else 0)
        if hasattr(sink, "error"):
            yield sink.error.eq(error if last else 0)
        yield sink.valid.eq(1)
        yield
        while not (yield sink.ready):
            yield
        yield sink.valid.eq(0)
        yield sink.last.eq(0)
        if hasattr(sink, "last_be"):
            yield sink.last_be.eq(0)
        if hasattr(sink, "error"):
            yield sink.error.eq(0)
        yield

# DUT
# -------------------------------------------------------------------------

class MACPacketReaderDUT(Module):
    def __init__(self, dw, depth, timestamp=None):
        self.packet = LiteEthMACPacketReader(dw, depth, timestamp=timestamp)
        self.submodules += self.packet
        self.source = self.packet.source

# Test MAC Packet Writer ---------------------------------------------------------------------------

class TestMACPacketWriter(unittest.TestCase):
    def _run_writer_drop_case(self, dw, data, eth_mtu, enable=1, error=0):
        dut    = LiteEthMACPacketWriter(dw, depth=8, eth_mtu=eth_mtu)
        result = {"drop": False, "error": None, "done": False, "source_valid": False, "timeout": False}

        def generator(timeout=128):
            yield dut.enable.eq(enable)
            yield dut.source.ready.eq(1)
            yield from mac_packet_send(dut.sink, dw, data, error=error)

            for _ in range(timeout):
                result["source_valid"] |= bool((yield dut.source.valid))
                result["done"]         |= bool((yield dut.done))
                if (yield dut.drop):
                    result["drop"]  = True
                    result["error"] = (yield dut.error)
                    return
                yield
            result["timeout"] = True

        run_simulation(dut, generator())
        return result

    def test_writer_good_packet(self):
        for dw, data in mac_packet_test_cases(length=10):
            with self.subTest(dw=dw):
                dut    = LiteEthMACPacketWriter(dw, depth=8, eth_mtu=eth_mtu_default)
                result = {
                    "data"        : [],
                    "offsets"     : [],
                    "write_count" : 0,
                    "done"        : False,
                    "length"      : None,
                    "timeout"     : False,
                }

                def generator(timeout=128):
                    yield dut.enable.eq(1)
                    yield dut.source.ready.eq(0)
                    yield from mac_packet_send(dut.sink, dw, data)
                    yield dut.source.ready.eq(1)

                    for _ in range(timeout):
                        if (yield dut.source.valid) and (yield dut.source.ready):
                            word    = (yield dut.source.data)
                            result["data"] += mac_packet_bytes(dw, word)
                            result["offsets"].append((yield dut.offset))
                            result["write_count"] += 1
                        if (yield dut.done):
                            result["done"]   = True
                            result["length"] = (yield dut.length)
                            return
                        yield
                    result["timeout"] = True

                run_simulation(dut, generator())
                self.assertFalse(result["timeout"])
                self.assertEqual(result["data"][:len(data)], data)
                self.assertEqual(result["offsets"], list(range(0, len(data), dw//8)))
                self.assertEqual(result["write_count"], len(mac_packet_words(dw, data)))
                self.assertTrue(result["done"])
                self.assertEqual(result["length"], len(data))

    def test_writer_disabled_packet_drops(self):
        for dw, data in mac_packet_test_cases(length=10):
            with self.subTest(dw=dw):
                result = self._run_writer_drop_case(
                    dw      = dw,
                    data    = data,
                    eth_mtu = eth_mtu_default,
                    enable  = 0,
                    error   = 0,
                )
                self.assertFalse(result["timeout"])
                self.assertTrue(result["drop"])
                self.assertEqual(result["error"], 0)
                self.assertFalse(result["done"])
                self.assertFalse(result["source_valid"])

    def test_writer_oversized_packet_drops_with_error(self):
        for dw, data in mac_packet_test_cases(length=10):
            with self.subTest(dw=dw):
                result = self._run_writer_drop_case(
                    dw      = dw,
                    data    = data,
                    eth_mtu = len(data) - 1,
                    enable  = 1,
                    error   = 0,
                )
                self.assertFalse(result["timeout"])
                self.assertTrue(result["drop"])
                self.assertEqual(result["error"], 1)
                self.assertFalse(result["done"])
                self.assertTrue(result["source_valid"])

    def test_writer_final_error_drops_with_error(self):
        for dw, data in mac_packet_test_cases(length=6):
            with self.subTest(dw=dw):
                result = self._run_writer_drop_case(
                    dw      = dw,
                    data    = data,
                    eth_mtu = eth_mtu_default,
                    enable  = 1,
                    error   = mac_packet_last_be(dw, len(data)),
                )
                self.assertFalse(result["timeout"])
                self.assertTrue(result["drop"])
                self.assertEqual(result["error"], 1)
                self.assertFalse(result["done"])
                self.assertTrue(result["source_valid"])

    def test_writer_timestamp_updates(self):
        for dw, data in mac_packet_test_cases(length=10):
            with self.subTest(dw=dw):
                timestamp       = Signal(32)
                timestamp_value = 0x22
                dut             = LiteEthMACPacketWriter(dw, depth=8, eth_mtu=eth_mtu_default, timestamp=timestamp)
                result          = {"timestamp": None, "timeout": False}

                def generator(timeout=128):
                    yield timestamp.eq(timestamp_value)
                    yield dut.enable.eq(1)
                    yield dut.source.ready.eq(0)
                    yield from mac_packet_send(dut.sink, dw, data)

                    yield dut.source.ready.eq(1)
                    result["timeout"] = (yield from wait_until(lambda: (yield dut.source.valid), timeout=timeout))
                    if result["timeout"]:
                        return
                    yield

                    for _ in range(timeout):
                        if (yield dut.done):
                            result["timestamp"] = (yield dut.timestamp)
                            return
                        yield
                    result["timeout"] = True

                run_simulation(dut, generator())
                self.assertFalse(result["timeout"])
                self.assertEqual(result["timestamp"], timestamp_value)

# Test MAC Packet Reader ---------------------------------------------------------------------------

class TestMACPacketReader(unittest.TestCase):
    def _run_reader_until_done(self, dw, data, timestamp=None, timestamp_value=0x55):
        dut    = MACPacketReaderDUT(dw, depth=8, timestamp=timestamp)
        result = {"data": [], "last_be": None, "done": False, "timeout": False}
        if timestamp is not None:
            result["timestamp"] = None

        def generator(timeout=128):
            if timestamp is not None:
                yield timestamp.eq(timestamp_value)
            yield dut.packet.length.eq(len(data))
            yield dut.source.ready.eq(0)
            yield dut.packet.enable.eq(1)
            yield
            yield dut.packet.enable.eq(0)
            yield from mac_packet_send(dut.packet.sink, dw, data)
            yield dut.source.ready.eq(1)

            for _ in range(timeout):
                if (yield dut.source.valid) and (yield dut.source.ready):
                    word    = (yield dut.source.data)
                    last    = (yield dut.source.last)
                    last_be = (yield dut.source.last_be)
                    result["data"] += mac_packet_bytes(dw, word, last, last_be)
                    if last:
                        result["last_be"] = last_be
                if (yield dut.packet.done):
                    result["done"] = True
                    if timestamp is not None:
                        result["timestamp"] = (yield dut.packet.timestamp)
                    return
                yield
            result["timeout"] = True

        run_simulation(dut, generator())
        return result

    def test_reader_packet_length_last_be_and_done(self):
        for dw, data in mac_packet_test_cases(length=10):
            with self.subTest(dw=dw):
                result = self._run_reader_until_done(
                    dw   = dw,
                    data = data,
                )

                self.assertFalse(result["timeout"])
                self.assertEqual(result["data"], data)
                self.assertEqual(result["last_be"], mac_packet_last_be(dw, len(data)))
                self.assertTrue(result["done"])

    def test_reader_timestamp_updates(self):
        for dw, data in mac_packet_test_cases(length=8):
            with self.subTest(dw=dw):
                timestamp       = Signal(32)
                timestamp_value = 0x55
                result          = self._run_reader_until_done(
                    dw              = dw,
                    data            = data,
                    timestamp       = timestamp,
                    timestamp_value = timestamp_value,
                )

                self.assertFalse(result["timeout"])
                self.assertEqual(result["timestamp"], timestamp_value)

    def test_reader_done_waits_for_final_source_accept(self):
        for dw, data in mac_packet_test_cases(length=4):
            with self.subTest(dw=dw):
                dut    = MACPacketReaderDUT(dw, depth=4)
                result = {"done_before_ready": False, "done_after_ready": False, "timeout": False}

                def generator(timeout=128):
                    yield dut.packet.length.eq(len(data))
                    yield dut.source.ready.eq(0)
                    yield dut.packet.enable.eq(1)
                    yield
                    yield dut.packet.enable.eq(0)
                    yield from mac_packet_send(dut.packet.sink, dw, data)

                    result["timeout"] = (yield from wait_until(lambda: (yield dut.source.valid) and (yield dut.source.last), timeout=timeout))
                    if result["timeout"]:
                        return
                    for _ in range(4):
                        result["done_before_ready"] |= bool((yield dut.packet.done))
                        yield

                    yield dut.source.ready.eq(1)
                    for _ in range(16):
                        if (yield dut.packet.done):
                            result["done_after_ready"] = True
                            return
                        yield
                    result["timeout"] = True

                run_simulation(dut, generator())
                self.assertFalse(result["timeout"])
                self.assertFalse(result["done_before_ready"])
                self.assertTrue(result["done_after_ready"])
