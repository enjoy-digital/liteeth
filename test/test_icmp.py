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

model_ip = convert_ip("192.168.10.1")
model_mac = 0x12345678abcd

dut_ip = convert_ip("192.168.10.50")
dut_mac = 0x12345678ffff

got_ping_reply = False

class ICMP(icmp.ICMP):
    def process(self, p):
        global got_ping_reply
        print("Received ping reply", p)
        if p.code == 0 and p.ident == 0x69b3 and p.checksum == 0xaac0 and p.sequence == 1:
            got_ping_reply = True
        else:
            raise RuntimeError("Invalid ping reply\nFAIL")


class DUT(Module):
    def __init__(self, dw=8):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.eth_tx = ClockDomain()
        self.clock_domains.eth_rx = ClockDomain()
        self.dw = dw

        self.submodules.phy_model = phy.PHY(self.dw, debug=False, pcap_file='dump.pcap')
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, model_mac, model_ip, debug=True)
        self.submodules.ip_model = ip.IP(self.mac_model, model_mac, model_ip, debug=False, loopback=False)
        self.submodules.icmp_model = ICMP(self.ip_model, model_ip, debug=True)

        self.submodules.ip = LiteEthIPCore(self.phy_model, dut_mac, dut_ip, 100000)


def send_icmp(dut, msgtype=icmp_type_ping_request, code=0):
    p = icmp.ICMPPacket(b"Hello World 123456")
    p.code = code
    p.checksum = 0
    p.msgtype = msgtype
    p.ident = 0x69b3
    p.sequence = 0x1
    dut.icmp_model.send(p, target_ip=dut_ip)


def main_generator(dut):
    global got_ping_reply

    # We expect a ping reply to this (after ARP query)
    send_icmp(dut)
    for i in range(512):
        yield
        if got_ping_reply:
            break

    if not got_ping_reply:
        raise RuntimeError("Missing ping reply\nFAIL")

    # We expect no ping reply to this
    got_ping_reply = False
    send_icmp(dut, 3, 3)
    for i in range(512):
        if got_ping_reply:
            raise RuntimeError("Inappropriate ping reply\nFAIL")
        yield

    print("PASS")


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
