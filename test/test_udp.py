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

from test.model import phy, mac, arp, ip, udp

from litex.gen.sim import *

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0x12345678
mac_address = 0x12345678abcd

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw=8):
        self.dw        = dw
        self.phy_model = phy.PHY(8, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.ip_model  = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
        self.udp_model = udp.UDP(self.ip_model, ip_address, debug=False, loopback=True)

        self.core     = LiteEthUDPIPCore(self.phy_model, mac_address, ip_address, 100000)
        udp_port      = self.core.udp.crossbar.get_port(0x5678, dw)
        self.streamer = PacketStreamer(eth_udp_user_description(dw))
        self.logger   = PacketLogger(eth_udp_user_description(dw))
        self.comb += [
            Record.connect(self.streamer.source, udp_port.sink),
            udp_port.sink.ip_address.eq(0x12345678),
            udp_port.sink.src_port.eq(0x1234),
            udp_port.sink.dst_port.eq(0x5678),
            udp_port.sink.length.eq(64//(dw//8)),
            Record.connect(udp_port.source, self.logger.sink)
        ]

# Generator ----------------------------------------------------------------------------------------

def main_generator(dut):
    packet = Packet([i for i in range(64//(dut.dw//8))])
    dut.streamer.send(packet)
    yield from dut.logger.receive()

    # check results
    s, l, e = check(packet, dut.logger.packet)
    print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))

# Test UDP -----------------------------------------------------------------------------------------

class TestUDP(unittest.TestCase):
    def test(self):
        dut = DUT(8)
        generators = {
            "sys"    : [
                main_generator(dut),
                dut.streamer.generator(),
                dut.logger.generator(),
            ],
            "eth_tx" : [
                dut.phy_model.phy_sink.generator(),
                dut.phy_model.generator(),
            ],
            "eth_rx" : [
                dut.phy_model.phy_source.generator()
            ]
        }
        clocks = {
            "sys"    : 10,
            "eth_rx" : 10,
            "eth_tx" : 10,
        }
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")
