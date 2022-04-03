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
from liteeth.core import LiteEthIPCore

from test.model.dumps import *
from test.model.mac import *
from test.model.ip import *
from test.model.icmp import *
from test.model import phy, mac, arp, ip, icmp

from litex.gen.sim import *

ip_address = 0x12345678
mac_address = 0x12345678abcd


class DUT(Module):
    def __init__(self):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.eth_tx = ClockDomain()
        self.clock_domains.eth_rx = ClockDomain()

        self.submodules.phy_model = phy.PHY(8, debug=True, pcap_file='dump.pcap')
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=True, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=True)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=True, loopback=False)
        self.submodules.icmp_model = icmp.ICMP(self.ip_model, ip_address, debug=True)

        self.submodules.ip = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000)


def send_overwrite_addresses(dump, dut):
    packet = MACPacket(dump)
    packet.decode_remove_header()
    packet = IPPacket(packet)
    packet.decode()
    packet = ICMPPacket(packet)
    packet.decode()
    dut.icmp_model.send(packet)


def main_generator(dut):
    # We expect no ping reply to this
    send_overwrite_addresses(icmp_unreachable_reply, dut)
    for i in range(512):
        yield

    # We expect a ping reply to this
    send_overwrite_addresses(ping_request, dut)
    for i in range(512):
        yield

class TestICMP(unittest.TestCase):
    def test(self):
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
