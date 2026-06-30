#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *
from litex.soc.interconnect import stream, wishbone

from liteeth.common import *
from liteeth.frontend.etherbone import LiteEthEtherbone

from test.model import etherbone
from test.stream_helpers import Packet, PacketLogger, PacketStreamer

# Constants ----------------------------------------------------------------------------------------

ip_address = 0x12345678
udp_port   = 0x1234

# Helpers ------------------------------------------------------------------------------------------

class UDPPort:
    def __init__(self, dw):
        self.sink   = stream.Endpoint(eth_udp_user_description(dw))
        self.source = stream.Endpoint(eth_udp_user_description(dw))


class UDPCrossbar:
    def __init__(self, dw):
        self.dw   = dw
        self.port = UDPPort(dw)

    def get_port(self, udp_port, dw=8, cd="sys", depth=None):
        assert dw == self.dw
        return self.port


class UDP:
    def __init__(self, dw):
        self.crossbar = UDPCrossbar(dw)


def encode_packet(packet):
    if not packet.encoded:
        packet.encode()
    return Packet(packet.bytes)


def decode_packet(packet):
    packet = etherbone.EtherbonePacket(init=bytes(packet))
    packet.decode()
    return packet

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self):
        self.udp       = UDP(32)
        self.etherbone = LiteEthEtherbone(self.udp, udp_port, buffer_depth=8)

        self.sram         = wishbone.SRAM(1024)
        self.interconnect = wishbone.InterconnectPointToPoint(self.etherbone.wishbone.bus, self.sram.bus)

        self.streamer = PacketStreamer(eth_udp_user_description(32), byte_data=True)
        self.logger   = PacketLogger(eth_udp_user_description(32), byte_data=True)
        udp_port_endpoint = self.udp.crossbar.port
        self.comb += [
            self.streamer.source.connect(udp_port_endpoint.source),
            udp_port_endpoint.sink.connect(self.logger.sink),
        ]

# Test Etherbone -----------------------------------------------------------------------------------

class TestEtherbone(unittest.TestCase):
    def send(self, dut, packet):
        packet = encode_packet(packet)
        yield dut.streamer.source.src_port.eq(udp_port)
        yield dut.streamer.source.dst_port.eq(udp_port)
        yield dut.streamer.source.ip_address.eq(ip_address)
        yield dut.streamer.source.length.eq(len(packet))
        yield from dut.streamer.send_blocking(packet)

    def receive(self, dut):
        yield from dut.logger.receive(timeout=256)
        self.assertTrue(dut.logger.packet.done)
        return decode_packet(dut.logger.packet)

    def do_probe(self, dut):
        packet = etherbone.EtherbonePacket()
        packet.pf = 1
        packet.encode()
        packet.bytes += bytes([0x00]*4)
        yield from self.send(dut, packet)

        response = yield from self.receive(dut)
        self.assertEqual(response.pr, 1)

    def do_writes(self, dut, datas):
        for i in range(len(datas)):
            yield dut.sram.mem[i].eq(0)

        writes = etherbone.EtherboneWrites(base_addr=0x0000, datas=datas)
        record = etherbone.EtherboneRecord()
        record.writes = writes

        packet = etherbone.EtherbonePacket()
        packet.records = [record]
        yield from self.send(dut, packet)

        for _ in range(64):
            values = []
            for i in range(len(datas)):
                values.append((yield dut.sram.mem[i]))
            if values == datas:
                break
            yield

        values = []
        for i in range(len(datas)):
            values.append((yield dut.sram.mem[i]))
        self.assertEqual(values, datas)

    def do_reads(self, dut, datas):
        for i, data in enumerate(datas):
            yield dut.sram.mem[i].eq(data)

        reads = etherbone.EtherboneReads(
            base_ret_addr = 0x0000,
            addrs         = [4*i for i in range(len(datas))])
        record = etherbone.EtherboneRecord()
        record.reads = reads

        packet = etherbone.EtherbonePacket()
        packet.records = [record]
        yield from self.send(dut, packet)

        response = yield from self.receive(dut)
        self.assertEqual(len(response.records), 1)
        self.assertIsNotNone(response.records[0].writes)
        self.assertEqual(response.records[0].writes.get_datas(), datas)

    def main_generator(self, dut):
        yield from self.do_probe(dut)
        yield from self.do_writes(dut, [0x12345678])
        yield from self.do_writes(dut, [0x0a000000 | i for i in range(4)])
        yield from self.do_reads(dut, [0x87654321])
        yield from self.do_reads(dut, [0x0b000000 | i for i in range(4)])

    def test_probe_write_read(self):
        dut = DUT()
        run_simulation(dut, [
            self.main_generator(dut),
            dut.streamer.generator(),
            dut.logger.generator(),
        ])
