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

from test.model import phy, mac, arp, ip

from litex.gen.sim import *

ip_address = 0x12345678
mac_address = 0x12345678abcd


class DUT(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=True)

        self.submodules.ip = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000)
        self.ip_port = self.ip.ip.crossbar.get_port(udp_protocol)


def main_generator(dut):
    yield dut.ip_port.sink.valid.eq(1)
    yield dut.ip_port.sink.last.eq(1)
    yield dut.ip_port.sink.ip_address.eq(0x12345678)
    yield dut.ip_port.sink.protocol.eq(udp_protocol)

    yield dut.ip_port.source.ready.eq(1)
    while not ((yield dut.ip_port.source.valid) and (yield dut.ip_port.source.last)):
        yield
    print("packet from IP 0x{:08x}".format((yield dut.ip_port.sink.ip_address)))


class TestIP(unittest.TestCase):
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
