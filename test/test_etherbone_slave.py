#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *
from litex.soc.interconnect import stream

from liteeth.common import *
from liteeth.frontend.etherbone import LiteEthEtherbone

from test.model import etherbone
from test.stream_helpers import Packet, PacketLogger, PacketStreamer

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0x12345678
udp_port    = 0x1234
src_port    = 0x5678
remote_ip   = 0xC0A80101
remote_port = 0x4321

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
    def __init__(self, remote_ip=None, remote_port=None, timeout=4096):
        self.udp       = UDP(32)
        self.etherbone = LiteEthEtherbone(self.udp, udp_port, mode="slave", buffer_depth=8,
            remote_ip   = remote_ip,
            remote_port = remote_port,
            timeout     = timeout,
        )

        self.streamer = PacketStreamer(eth_udp_user_description(32), byte_data=True)
        self.logger   = PacketLogger(eth_udp_user_description(32), byte_data=True)
        self.tx_port  = udp_port_endpoint = self.udp.crossbar.port
        self.comb += [
            self.streamer.source.connect(udp_port_endpoint.source),
            udp_port_endpoint.sink.connect(self.logger.sink),
        ]
        self.tx_params = []

    @passive
    def tx_params_logger(self):
        first = True
        while True:
            if (yield self.tx_port.sink.valid) and (yield self.tx_port.sink.ready):
                if first:
                    self.tx_params.append((
                        (yield self.tx_port.sink.ip_address),
                        (yield self.tx_port.sink.src_port),
                        (yield self.tx_port.sink.dst_port),
                    ))
                first = (yield self.tx_port.sink.last)
            yield

# Test Etherbone Slave -----------------------------------------------------------------------------

class TestEtherboneSlave(unittest.TestCase):
    def send(self, dut, packet, ip=ip_address, port=src_port):
        packet = encode_packet(packet)
        yield dut.streamer.source.src_port.eq(port)
        yield dut.streamer.source.dst_port.eq(udp_port)
        yield dut.streamer.source.ip_address.eq(ip)
        yield dut.streamer.source.length.eq(len(packet))
        yield from dut.streamer.send_blocking(packet)

    def receive(self, dut):
        yield from dut.logger.receive(timeout=256)
        self.assertTrue(dut.logger.packet.done)
        return decode_packet(dut.logger.packet)

    def wb_write(self, dut, adr, dat_w, timeout=256):
        bus = dut.etherbone.wishbone.bus
        yield bus.cyc.eq(1)
        yield bus.stb.eq(1)
        yield bus.we.eq(1)
        yield bus.adr.eq(adr)
        yield bus.sel.eq(0xf)
        yield bus.dat_w.eq(dat_w)
        ack = 0
        for _ in range(timeout):
            yield
            ack = (yield bus.ack)
            if ack:
                break
        err = (yield bus.err)
        yield bus.cyc.eq(0)
        yield bus.stb.eq(0)
        yield
        return ack, err

    def wb_read_start(self, dut, adr):
        bus = dut.etherbone.wishbone.bus
        yield bus.cyc.eq(1)
        yield bus.stb.eq(1)
        yield bus.we.eq(0)
        yield bus.adr.eq(adr)
        yield bus.sel.eq(0xf)

    def wb_wait_ack(self, dut, timeout):
        bus = dut.etherbone.wishbone.bus
        for cycles in range(1, timeout + 1):
            yield
            if (yield bus.ack):
                return {
                    "ack":    1,
                    "err":    (yield bus.err),
                    "dat_r":  (yield bus.dat_r),
                    "cycles": cycles,
                }
        return {"ack": 0, "err": 0, "dat_r": 0, "cycles": timeout}

    def wb_stop(self, dut):
        bus = dut.etherbone.wishbone.bus
        yield bus.cyc.eq(0)
        yield bus.stb.eq(0)
        yield

    def run_dut(self, dut, main_generator):
        run_simulation(dut, [
            main_generator(dut),
            dut.streamer.generator(),
            dut.logger.generator(),
            dut.tx_params_logger(),
        ])

    def test_remote_write_read(self):
        # Check the write record address and byte enable against the bus.
        dut = DUT(remote_ip=remote_ip, remote_port=remote_port)
        def main_generator(dut):
            ack, err = yield from self.wb_write(dut, adr=0x4, dat_w=0x12345678)
            self.assertTrue(ack)
            self.assertFalse(err)
            packet = yield from self.receive(dut)
            self.assertEqual(len(packet.records), 1)
            self.assertEqual(packet.records[0].writes.get_datas(), [0x12345678])
            # Address and byte enable must come from the bus, not constants.
            self.assertEqual(packet.records[0].writes.base_addr, 0x10)  # 0x4 << 2
            self.assertEqual(packet.records[0].byte_enable, 0xf)
            
            yield from self.wb_read_start(dut, adr=0x8)
            packet = yield from self.receive(dut)
            self.assertEqual(len(packet.records), 1)
            self.assertEqual(packet.records[0].reads.base_ret_addr, 0x0000)
            self.assertEqual(packet.records[0].reads.get_addrs(), [0x20])
            
            writes = etherbone.EtherboneWrites(base_addr=0x0000, datas=[0x87654321])
            record = etherbone.EtherboneRecord()
            record.writes = writes
            response = etherbone.EtherbonePacket()
            response.records = [record]
            yield from self.send(dut, response)
            res = yield from self.wb_wait_ack(dut, timeout=1024)
            yield from self.wb_stop(dut)
            self.assertTrue(res["ack"])
            self.assertFalse(res["err"])
            self.assertEqual(res["dat_r"], 0x87654321)
        self.run_dut(dut, main_generator)
        self.assertEqual(len(dut.tx_params), 2)
        for tx_ip, tx_src_port, tx_dst_port in dut.tx_params:
            self.assertEqual(tx_ip, remote_ip)
            self.assertEqual(tx_src_port, udp_port)
            self.assertEqual(tx_dst_port, remote_port)

    def test_remote_redirect(self):
        dut = DUT(remote_ip=remote_ip, remote_port=remote_port)
        def main_generator(dut):
            writes = etherbone.EtherboneWrites(base_addr=0x0000, datas=[0xAAAAAAAA])
            record = etherbone.EtherboneRecord()
            record.writes = writes
            packet = etherbone.EtherbonePacket()
            packet.records = [record]
            yield from self.send(dut, packet, ip=0x0A000002, port=0x9999)
            for _ in range(64):
                yield
            ack, err = yield from self.wb_write(dut, adr=0x4, dat_w=0x55555555)
            self.assertTrue(ack)
            self.assertFalse(err)
            packet = yield from self.receive(dut)
            self.assertEqual(packet.records[0].writes.get_datas(), [0x55555555])
        self.run_dut(dut, main_generator)
        self.assertEqual(len(dut.tx_params), 1)
        tx_ip, tx_src_port, tx_dst_port = dut.tx_params[0]
        self.assertEqual(tx_ip, remote_ip)
        self.assertEqual(tx_src_port, udp_port)
        self.assertEqual(tx_dst_port, remote_port)

    def test_read_timeout(self):
        dut = DUT(remote_ip=remote_ip, remote_port=remote_port, timeout=256)
        def main_generator(dut):
            yield from self.wb_read_start(dut, adr=0x8)
            res = yield from self.wb_wait_ack(dut, timeout=2048)
            yield from self.wb_stop(dut)
            self.assertTrue(res["ack"])
            self.assertTrue(res["err"])
            self.assertGreaterEqual(res["cycles"], 256)
            self.assertLess(res["cycles"], 2048)
        self.run_dut(dut, main_generator)

    def test_read_timeout_then_recovery(self):
        # The timer must be re-armed on every read request.
        # Without the reset the second read would error out immediately.
        dut = DUT(remote_ip=remote_ip, remote_port=remote_port, timeout=128)
        def main_generator(dut):
            # First read times out.
            yield from self.wb_read_start(dut, adr=0x8)
            res1 = yield from self.wb_wait_ack(dut, timeout=512)
            yield from self.wb_stop(dut)
            self.assertTrue(res1["err"])
            
            # Second read must get a full window, not an immediate error.
            yield from self.wb_read_start(dut, adr=0x8)
            packet = yield from self.receive(dut)
            writes = etherbone.EtherboneWrites(base_addr=0x0000, datas=[0xDEADBEEF])
            record = etherbone.EtherboneRecord(); record.writes = writes
            response = etherbone.EtherbonePacket(); response.records = [record]
            yield from self.send(dut, response, ip=remote_ip, port=remote_port)
            
            res2 = yield from self.wb_wait_ack(dut, timeout=512)
            yield from self.wb_stop(dut)
            self.assertFalse(res2["err"])
            self.assertEqual(res2["dat_r"], 0xDEADBEEF)
        self.run_dut(dut, main_generator)

    def test_read_abort_by_master(self):
        # Dropping cyc during WAIT_READ must return the FSM to IDLE.
        dut = DUT(remote_ip=remote_ip, remote_port=remote_port, timeout=1024)
        def main_generator(dut):
            bus = dut.etherbone.wishbone.bus
            yield bus.cyc.eq(1)
            yield bus.stb.eq(1)
            yield bus.we.eq(0)
            yield bus.adr.eq(0x8)
            yield bus.sel.eq(0xf)
            
            # The master aborts the cycle after 32 cycles.
            for _ in range(32):
                yield
            
            yield bus.cyc.eq(0)
            yield bus.stb.eq(0)
            yield
            
            # The FSM must be idle again: a following write must complete.
            ack, err = yield from self.wb_write(dut, adr=0x4, dat_w=0x12345678)
            self.assertTrue(ack)
            self.assertFalse(err)
        self.run_dut(dut, main_generator)

    def test_remote_ip_zero(self):
        # remote_ip=0 is a valid setting: the check must be "is None".
        # A truthiness check would fall back to last_ip_address here.
        dut = DUT(remote_ip=0, remote_port=remote_port)
        def main_generator(dut):
            ack, err = yield from self.wb_write(dut, adr=0x4, dat_w=0x12345678)
            self.assertTrue(ack)
            packet = yield from self.receive(dut)
            self.assertEqual(len(packet.records), 1)
        self.run_dut(dut, main_generator)
        self.assertEqual(len(dut.tx_params), 1)
        tx_ip, tx_src_port, tx_dst_port = dut.tx_params[0]
        self.assertEqual(tx_ip, 0)

    def test_remote_params_master_rejected(self):
        # Both remote_ip and remote_port are rejected in master mode.
        with self.assertRaises(ValueError):
            LiteEthEtherbone(UDP(32), udp_port, mode="master", remote_ip=remote_ip)
        with self.assertRaises(ValueError):
            LiteEthEtherbone(UDP(32), udp_port, mode="master", remote_port=remote_port)
