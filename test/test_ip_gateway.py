#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *
from litex.soc.interconnect import stream

from liteeth.common import *
from liteeth.core.ip import LiteEthIPTX

# DUT ----------------------------------------------------------------------------------------------

class ARPTable:
    def __init__(self):
        self.request  = stream.Endpoint(arp_table_request_layout)
        self.response = stream.Endpoint(arp_table_response_layout)

class DUT(LiteXModule):
    def __init__(self, gateway_ip=None, netmask=None):
        self.arp_table = ARPTable()
        self.tx        = LiteEthIPTX(
            mac_address = 0x10e2d5000000,
            ip_address  = convert_ip("192.168.1.50"),
            arp_table   = self.arp_table,
            dw          = 8,
            gateway_ip  = gateway_ip,
            netmask     = netmask,
        )

# Test IP Gateway ----------------------------------------------------------------------------------

class TestIPGateway(unittest.TestCase):
    def check_arp_request_ip(self, dut, destination_ip, expected_arp_ip):
        destination_ip  = convert_ip(destination_ip)
        expected_arp_ip = convert_ip(expected_arp_ip)

        def generator():
            yield dut.tx.sink.valid.eq(1)
            yield dut.tx.sink.last.eq(1)
            yield dut.tx.sink.last_be.eq(1)
            yield dut.tx.sink.length.eq(1)
            yield dut.tx.sink.protocol.eq(udp_protocol)
            yield dut.tx.sink.ip_address.eq(destination_ip)

            for _ in range(64):
                if (yield dut.arp_table.request.valid):
                    self.assertEqual((yield dut.arp_table.request.ip_address), expected_arp_ip)
                    return
                yield
            raise TimeoutError

        run_simulation(dut, generator())

    def test_default_arp_request_uses_destination_ip(self):
        dut = DUT()
        self.check_arp_request_ip(
            dut             = dut,
            destination_ip  = "10.0.0.1",
            expected_arp_ip = "10.0.0.1",
        )

    def test_gateway_arp_request_uses_destination_ip_on_subnet(self):
        dut = DUT(gateway_ip="192.168.1.1", netmask="255.255.255.0")
        self.check_arp_request_ip(
            dut             = dut,
            destination_ip  = "192.168.1.100",
            expected_arp_ip = "192.168.1.100",
        )

    def test_gateway_arp_request_uses_gateway_ip_off_subnet(self):
        dut = DUT(gateway_ip="192.168.1.1", netmask="255.255.255.0")
        self.check_arp_request_ip(
            dut             = dut,
            destination_ip  = "10.0.0.1",
            expected_arp_ip = "192.168.1.1",
        )
