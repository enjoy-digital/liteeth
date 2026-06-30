#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from liteeth.common import *
from liteeth.core import LiteEthIPCore

from test.model import phy, mac, arp, ip, icmp

from litex.gen.sim import *

# Constants ----------------------------------------------------------------------------------------

model_ip  = convert_ip("192.168.10.1")
model_mac = 0x12345678abcd

dut_ip    = convert_ip("192.168.10.50")
dut_mac   = 0x12345678ffff

# ICMP Model ---------------------------------------------------------------------------------------

class ICMP(icmp.ICMP):
    def __init__(self, ip, ip_address, replies, debug=False):
        self.replies = replies
        icmp.ICMP.__init__(self, ip, ip_address, debug=debug)

    def process(self, packet):
        self.replies.append(packet)

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw=8, eth_mtu=eth_mtu_default):
        self.icmp_replies = []

        self.phy_model  = phy.PHY(dw, debug=False)
        self.mac_model  = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model  = arp.ARP(self.mac_model, model_mac, model_ip, debug=False)
        self.ip_model   = ip.IP(self.mac_model, model_mac, model_ip, debug=False, loopback=False)
        self.icmp_model = ICMP(self.ip_model, model_ip, self.icmp_replies, debug=False)

        self.ip = LiteEthIPCore(self.phy_model, dut_mac, dut_ip, 100000, dw=dw, eth_mtu=eth_mtu)

# Generator ----------------------------------------------------------------------------------------

def expected_ping_reply(payload, quench):
    packet = icmp.ICMPPacket(payload)
    packet.msgtype  = icmp_type_ping_reply
    packet.code     = 0
    packet.checksum = 0
    packet.quench   = quench
    packet.encode()
    packet.insert_checksum()
    packet = icmp.ICMPPacket(packet)
    packet.decode()
    return packet

def main_generator(dut):
    tc = unittest.TestCase()

    payload = list(b"Hello World 123456")
    quench  = 0x69b30001
    request = icmp.ICMPPacket(payload)
    request.msgtype  = icmp_type_ping_request
    request.code     = 0
    request.checksum = 0
    request.quench   = quench

    dut.icmp_model.send(request, target_ip=dut_ip)

    for _ in range(512):
        if dut.icmp_replies:
            break
        yield

    tc.assertEqual(len(dut.icmp_replies), 1)
    reply    = dut.icmp_replies[0]
    expected = expected_ping_reply(payload, quench)

    tc.assertEqual(reply.msgtype,  icmp_type_ping_reply)
    tc.assertEqual(reply.code,     0)
    tc.assertEqual(reply.checksum, expected.checksum)
    tc.assertEqual(reply.quench,   quench)
    tc.assertEqual(list(reply),    payload)

# Test ICMP ----------------------------------------------------------------------------------------

class TestICMP(unittest.TestCase):
    def run_test(self, dw):
        dut = DUT(dw=dw)
        generators = {
            "sys"    : [main_generator(dut)],
            "eth_tx" : [dut.phy_model.phy_sink.generator(), dut.phy_model.generator()],
            "eth_rx" : [dut.phy_model.phy_source.generator()],
        }
        clocks = {
            "sys":    10,
            "eth_rx": 10,
            "eth_tx": 10,
        }
        run_simulation(dut, generators, clocks)

    def test_echo_dw_8(self):
        self.run_test(8)
