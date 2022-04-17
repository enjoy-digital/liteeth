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
DW = 32

tc = unittest.TestCase()


class DUT(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(DW, debug=False, pcap_file='dump_wishbone.pcap')
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
        self.submodules.udp_model = udp.UDP(self.ip_model, ip_address, debug=False, loopback=False)
        self.submodules.etherbone_model = etherbone.Etherbone(self.udp_model, debug=True)

        self.submodules.core = LiteEthUDPIPCore(self.phy_model, mac_address + 1, ip_address + 1, 100000, dw=DW)
        self.submodules.etherbone = LiteEthEtherbone(self.core.udp, 0x1234, buffer_depth=8)

        self.submodules.sram = wishbone.SRAM(1024)
        self.submodules.interconnect = wishbone.InterconnectPointToPoint(self.etherbone.wishbone.bus, self.sram.bus)


def test_probe(dut):
    packet = etherbone.EtherbonePacket()
    packet.pf = 1
    packet.encode()
    packet.bytes += bytes([0x00, 0x00, 0x00, 0x00])  # Add payload padding

    dut.etherbone_model.send(packet, ip_address + 1)
    yield from dut.etherbone_model.receive(400)
    tc.assertEqual(dut.etherbone_model.rx_packet.pr, 1)


def test_writes(dut, writes_datas):
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
        tc.assertEqual(val, wd)


def test_reads(dut, writes_datas):
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

    reads_datas = dut.etherbone_model.rx_packet.records.pop().writes.get_datas()

    # check results
    tc.assertEqual(writes_datas, reads_datas)


def main_generator(dut):
    writes_datas = [((0xA + j) << 28) + j for j in range(6)]
    yield from test_probe(dut)
    yield from test_writes(dut, writes_datas)
    yield from test_reads(dut, writes_datas)


class TestEtherbone(unittest.TestCase):
    def test_etherbone(self):
        dut = DUT()
        generators = {
            "sys" :   [main_generator(dut)],
            "eth_tx": [dut.phy_model.phy_sink.generator(),
                       dut.phy_model.generator()],
            "eth_rx":  dut.phy_model.phy_source.generator()
        }
        clocks = {"sys":    10,
                  "eth_rx": 10,
                  "eth_tx": 10}
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")
