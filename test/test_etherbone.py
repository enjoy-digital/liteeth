#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.core import LiteEthUDPIPCore
from liteeth.frontend.etherbone import LiteEthEtherbone
from test.model import phy, mac, arp, ip, udp, etherbone
from litex.gen.sim import *


ip_address = 0x12345678
mac_address = 0x12345678abcd


class DUT(Module):
    def __init__(self, dw):
        self.submodules.phy_model = phy.PHY(dw, assertStall=dw < 64, pcap_file='dump_wishbone.pcap')
        self.submodules.mac_model = mac.MAC(self.phy_model)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address)
        self.submodules.udp_model = udp.UDP(self.ip_model, ip_address)
        self.submodules.etherbone_model = etherbone.Etherbone(self.udp_model, debug=False)

        self.submodules.core = LiteEthUDPIPCore(
            self.phy_model,
            mac_address + 1,
            ip_address + 1,
            100000,
            with_icmp=False,
            dw=dw
        )
        self.submodules.etherbone = LiteEthEtherbone(self.core.udp, 0x1234, buffer_depth=8)

        self.submodules.sram = wishbone.SRAM(1024)
        self.submodules.interconnect = wishbone.InterconnectPointToPoint(self.etherbone.wishbone.bus, self.sram.bus)


class TestEtherbone(unittest.TestCase):
    def do_probe(self, dut):
        packet = etherbone.EtherbonePacket()
        packet.pf = 1
        packet.encode()
        packet.bytes += bytes([0x00] * 4)  # Add padding

        dut.etherbone_model.send(packet, ip_address + 1)
        yield from dut.etherbone_model.receive(400)
        self.assertEqual(dut.etherbone_model.rx_packet.pr, 1)

    def do_writes(self, dut, writes_datas):
        for i, wd in enumerate(writes_datas):
            yield dut.sram.mem[i].eq(0)

        writes = etherbone.EtherboneWrites(base_addr=0x1000, datas=writes_datas)
        record = etherbone.EtherboneRecord()
        record.writes      = writes
        record.reads       = None
        record.bca         = 0
        record.rca         = 0
        record.rff         = 0
        record.cyc         = 0
        record.wca         = 0
        record.wff         = 0
        record.byte_enable = 0xf
        record.wcount      = len(writes_datas)
        record.rcount      = 0

        packet = etherbone.EtherbonePacket()
        packet.records = [record]
        dut.etherbone_model.send(packet)

        for i in range(256):
            yield
            if (yield dut.sram.bus.cyc):
                break
        for i in range(32):
            yield

        for i, wd in enumerate(writes_datas):
            val = (yield dut.sram.mem[i])
            self.assertEqual(val, wd)

        # Check for infinite packet send loop (last_be bug in StrideConverter)
        self.assertEqual((yield dut.etherbone.record.receiver.fsm.state), 0)

    def do_reads(self, dut, writes_datas):
        reads_addrs = []
        for i, wd in enumerate(writes_datas):
            yield dut.sram.mem[i].eq(wd)
            reads_addrs.append(0x1000 + i * 4)

        reads = etherbone.EtherboneReads(base_ret_addr=0x1000, addrs=reads_addrs)
        record = etherbone.EtherboneRecord()
        record.writes      = None
        record.reads       = reads
        record.bca         = 0
        record.rca         = 0
        record.rff         = 0
        record.cyc         = 0
        record.wca         = 0
        record.wff         = 0
        record.byte_enable = 0xf
        record.wcount      = 0
        record.rcount      = len(reads_addrs)

        packet = etherbone.EtherbonePacket()
        packet.records = [record]
        dut.etherbone_model.send(packet)
        yield from dut.etherbone_model.receive(400)

        # Check for infinite packet send loop (crossbar bug)
        self.assertEqual((yield dut.etherbone.record.receiver.fsm.state), 0)

        reads_datas = dut.etherbone_model.rx_packet.records.pop().writes.get_datas()

        # check results
        self.assertEqual(writes_datas, reads_datas)

    def main_generator(self, dut):
        writes_datas = [((0xA + j) << 24) + j for j in range(4)]

        # push IP address into ARP table to speed up sim.
        yield dut.core.arp.table.cached_valid.eq(1)
        yield dut.core.arp.table.cached_ip_address.eq(ip_address)
        yield dut.core.arp.table.cached_mac_address.eq(mac_address)

        with self.subTest("do_probe"):
            yield from self.do_probe(dut)
        with self.subTest("do_writes"):
            yield from self.do_writes(dut, [writes_datas[0]])
            yield from self.do_writes(dut, writes_datas)
        with self.subTest("do_reads"):
            yield from self.do_reads(dut, [writes_datas[0]])
            yield from self.do_reads(dut, writes_datas)

    def do_test(self, dut):
        generators = {
            "sys" :   [self.main_generator(dut)],
            "eth_tx": [dut.phy_model.phy_sink.generator(),
                       dut.phy_model.generator()],
            "eth_rx":  dut.phy_model.phy_source.generator()
        }
        clocks = {"sys":    10,
                  "eth_rx": 10,
                  "eth_tx": 10}
        run_simulation(dut, generators, clocks, vcd_name=f'test_etherbone.vcd')

    def test_etherbone_dw_8(self):
        self.do_test(DUT(8))

    def test_etherbone_dw_32(self):
        self.do_test(DUT(32))

    def test_etherbone_dw_64(self):
        self.do_test(DUT(64))
