#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from test.stream_helpers import *

from liteeth.common import *
from liteeth.mac import LiteEthMAC
from liteeth.core.arp import LiteEthARP, LiteEthARPCache

from test.model import phy, mac, arp

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0x12345678
mac_address = 0x12345678abcd

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, eth_mtu=eth_mtu_default):
        self.phy_model = phy.PHY(8, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)

        self.mac = LiteEthMAC(self.phy_model, dw=8, with_preamble_crc=True, eth_mtu=eth_mtu)
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
        for mtu in [eth_mtu_default, eth_mtu_jumboframe]:
            with self.subTest(eth_mtu=mtu):
                dut = DUT(eth_mtu=mtu)
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

# Test ARP Cache -----------------------------------------------------------------------------------

class TestARPCache(unittest.TestCase):
    def cache_test(self, entries, updates, requests):
        dut     = LiteEthARPCache(entries=entries, clk_freq=100e6)
        results = []

        def generator():
            yield dut.clear_enable.eq(0)
            yield dut.response.ready.eq(1)
            for _ in range(2*entries):
                yield
            for ip, mac in updates:
                yield dut.update.valid.eq(1)
                yield dut.update.ip_address.eq(ip)
                yield dut.update.mac_address.eq(mac)
                yield
                while (yield dut.update.ready) != 1:
                    yield
                yield dut.update.valid.eq(0)
                yield
            for ip in requests:
                yield dut.request.valid.eq(1)
                yield dut.request.ip_address.eq(ip)
                yield
                while (yield dut.response.valid) != 1:
                    yield
                results.append(((yield dut.response.error), (yield dut.response.mac_address)))
                yield dut.request.valid.eq(0)
                yield

        run_simulation(dut, generator())
        return results

    def test_cache_hits_return_matching_mac(self):
        for entries in [2, 4]:
            updates  = [(0xc0a80101 + i, 0x020000000001 + i) for i in range(entries)]
            for order in ["direct", "reversed"]:
                with self.subTest(entries=entries, order=order):
                    _updates = updates if order == "direct" else updates[::-1]
                    requests = [ip for ip, _ in updates] + [0xc0a801ff]
                    results  = self.cache_test(entries, _updates, requests)
                    self.assertEqual(results, [(0, mac) for _, mac in updates] + [(1, results[-1][1])])
