#
# This file is part of LiteX ecosystem tests.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import math
import random
from copy import deepcopy

from migen import *

from litex.soc.interconnect import stream

__all__ = [
    "print_with_prefix",
    "seed_to_data",
    "split_bytes",
    "merge_bytes",
    "get_field_data",
    "comp",
    "check",
    "randn",
    "Packet",
    "PacketStreamer",
    "PacketLogger",
    "Randomizer",
]


def print_with_prefix(s, prefix=""):
    if not isinstance(s, str):
        s = repr(s)
    for line in s.split("\n"):
        print(prefix + line)


def seed_to_data(seed, random=True):
    if random:
        return (seed*0x31415979 + 1) & 0xffffffff
    return seed


def split_bytes(v, n, endianness="big"):
    return [int(byte) for byte in v.to_bytes(n, byteorder=endianness)]


def merge_bytes(b, endianness="big"):
    return int.from_bytes(bytes(b), endianness)


def get_field_data(field, datas):
    v = merge_bytes(datas[field.byte:field.byte + math.ceil(field.width/8)])
    return (v >> field.offset) & (2**field.width - 1)


def comp(p1, p2):
    return all(x == y for x, y in zip(p1, p2))


def check(p1, p2):
    p1 = deepcopy(p1)
    p2 = deepcopy(p2)
    if isinstance(p1, int):
        return 0, 1, int(p1 != p2)

    if len(p1) >= len(p2):
        ref, res = p1, p2
    else:
        ref, res = p2, p1
    shift = 0
    while (ref[0] != res[0]) and (len(res) > 1):
        res.pop(0)
        shift += 1
    length = min(len(ref), len(res))
    errors = 0
    for _ in range(length):
        if ref.pop(0) != res.pop(0):
            errors += 1
    return shift, length, errors


def randn(max_n):
    return random.randint(0, max_n - 1)


class Packet(list):
    def __init__(self, init=None):
        self.ongoing = False
        self.done    = False
        if init is not None:
            for data in init:
                self.append(data)


class PacketStreamer(Module):
    def __init__(self, description, last_be=None, packet_cls=Packet, byte_data=False):
        self.source = stream.Endpoint(description)
        self.last_be = last_be
        self.byte_data = byte_data

        self.packets = []
        self.packet  = packet_cls()
        self.packet.done = True

    def _pop_word(self):
        bytes_per_clk = len(self.source.data)//8
        nbytes = min(bytes_per_clk, len(self.packet))
        data = 0
        for i in range(nbytes):
            data |= self.packet.pop(0) << (8*i)
        last = len(self.packet) == 0
        last_be = 1 << (nbytes - 1)
        return data, last, last_be

    def send(self, packet):
        packet = deepcopy(packet)
        self.packets.append(packet)
        return packet

    def send_blocking(self, packet):
        packet = self.send(packet)
        while not packet.done:
            yield

    @passive
    def generator(self):
        while True:
            if len(self.packets) and self.packet.done:
                self.packet = self.packets.pop(0)
            if self.byte_data:
                if not self.packet.ongoing and not self.packet.done:
                    data, last, last_be = self._pop_word()
                    yield self.source.valid.eq(1)
                    yield self.source.data.eq(data)
                    yield self.source.last.eq(last)
                    if hasattr(self.source, "last_be"):
                        yield self.source.last_be.eq(last_be if last else 0)
                    self.packet.ongoing = True
                elif (yield self.source.valid) and (yield self.source.ready):
                    if len(self.packet):
                        data, last, last_be = self._pop_word()
                        yield self.source.valid.eq(1)
                        yield self.source.data.eq(data)
                        yield self.source.last.eq(last)
                        if hasattr(self.source, "last_be"):
                            yield self.source.last_be.eq(last_be if last else 0)
                    else:
                        self.packet.done = True
                        yield self.source.valid.eq(0)
                        yield self.source.last.eq(0)
                        if hasattr(self.source, "last_be"):
                            yield self.source.last_be.eq(0)
                yield
                continue
            if not self.packet.ongoing and not self.packet.done:
                yield self.source.valid.eq(1)
                yield self.source.data.eq(self.packet.pop(0))
                self.packet.ongoing = True
            elif (yield self.source.valid) and (yield self.source.ready):
                yield self.source.last.eq(len(self.packet) == 1)
                if self.last_be is not None:
                    yield self.source.last_be.eq(self.last_be & (len(self.packet) == 1))
                if len(self.packet):
                    yield self.source.valid.eq(1)
                    yield self.source.data.eq(self.packet.pop(0))
                else:
                    self.packet.done = True
                    yield self.source.valid.eq(0)
            yield


class PacketLogger(Module):
    def __init__(self, description, packet_cls=Packet, byte_data=False):
        self.sink = stream.Endpoint(description)
        self.byte_data = byte_data

        self.packet_cls = packet_cls
        self.packet     = packet_cls()
        self.first      = True

    def receive(self, length=None, timeout=None):
        self.packet.done = False
        cycles = 0
        if length is None:
            while not self.packet.done:
                if timeout is not None and cycles >= timeout:
                    break
                cycles += 1
                yield
        else:
            while length > len(self.packet):
                if timeout is not None and cycles >= timeout:
                    break
                cycles += 1
                yield

    @passive
    def generator(self):
        while True:
            yield self.sink.ready.eq(1)
            if (yield self.sink.valid):
                if self.first:
                    self.packet = self.packet_cls()
                    self.first = False
                data = (yield self.sink.data)
                if self.byte_data:
                    bytes_per_clk = len(self.sink.data)//8
                    nbytes = bytes_per_clk
                    if (yield self.sink.last) and hasattr(self.sink, "last_be"):
                        last_be = (yield self.sink.last_be)
                        if last_be != 0:
                            nbytes = last_be.bit_length()
                    for i in range(nbytes):
                        self.packet.append((data >> (8*i)) & 0xff)
                else:
                    self.packet.append(data)
                if (yield self.sink.last):
                    self.packet.done = True
                    self.first = True
            yield


class Randomizer(Module):
    def __init__(self, description, level=0):
        self.level = level

        self.sink   = stream.Endpoint(description)
        self.source = stream.Endpoint(description)

        self.ce = Signal(reset=1)

        self.comb += If(self.ce,
            self.sink.connect(self.source)
        ).Else(
            self.source.valid.eq(0),
            self.sink.ready.eq(0),
        )

    @passive
    def generator(self):
        while True:
            if randn(100) < self.level:
                yield self.ce.eq(0)
            else:
                yield self.ce.eq(1)
            yield
