#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.mac import LiteEthMAC
from liteeth.core.arp import LiteEthARP

from test.model import phy, mac, arp

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0x12345678
mac_address = 0x12345678abcd

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self):
        self.phy_model = phy.PHY(8, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)

        self.mac = LiteEthMAC(self.phy_model, dw=8, with_preamble_crc=True)
        self.arp = LiteEthARP(self.mac, mac_address, ip_address, 100000)

# Genrator -----------------------------------------------------------------------------------------

def main_generator(dut):
    while (yield dut.arp.table.request.ready) != 1:
        yield dut.arp.table.request.valid.eq(1)
        yield dut.arp.table.request.ip_address.eq(0x12345678)
        yield
    yield dut.arp.table.request.valid.eq(0)
    while (yield dut.arp.table.response.valid) != 1:
        yield dut.arp.table.response.ready.eq(1)
        yield
    print("Received MAC : 0x{:12x}".format((yield dut.arp.table.response.mac_address)))

# Test ARP -----------------------------------------------------------------------------------------

class TestARP(unittest.TestCase):
    def test(self):
        dut = DUT()
        generators = {
            "sys"    : [main_generator(dut)],
            "eth_tx" : [dut.phy_model.phy_sink.generator(), dut.phy_model.generator()],
            "eth_rx" : [dut.phy_model.phy_source.generator()],
        }
        clocks = {
            "sys"    : 10,
            "eth_rx" : 10,
            "eth_tx" : 10,
        }
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")
