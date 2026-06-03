#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen.sim import *
from litex.soc.interconnect import stream

from liteeth.common import *
from liteeth.core.dhcp import *

# Constants ----------------------------------------------------------------------------------------

mac_address        = 0x10e2d5000001
transaction_id     = 0x12345678
offered_ip_address = convert_ip("192.168.1.100")
server_ip_address  = convert_ip("192.168.1.1")

# Helpers ------------------------------------------------------------------------------------------

def split_word(word):
    return [(word >> (8*i)) & 0xff for i in range(4)]

def split_be(value, nbytes):
    return [(value >> (8*(nbytes - 1 - i))) & 0xff for i in range(nbytes)]

def merge_be(data):
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value

def parse_options(data):
    options = {}
    offset  = 0
    while offset < len(data):
        code = data[offset]
        offset += 1
        if code == DHCP_OPTTYP_END:
            break
        if code == 0:
            continue
        length = data[offset]
        offset += 1
        options[code] = data[offset:offset + length]
        offset += length
    return options

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self):
        self.udp_port        = udp_port = type("UDPPort", (), {})()
        self.udp_port.sink   = stream.Endpoint(eth_udp_user_description(32))
        self.udp_port.source = stream.Endpoint(eth_udp_user_description(32))

        self.tx = LiteEthDHCPTX(udp_port)
        self.rx = LiteEthDHCPRX(udp_port)

# Test DHCP TX -------------------------------------------------------------------------------------

class TestDHCPTX(unittest.TestCase):
    def capture_packet(self, packet_type):
        dut      = DUT()
        packet   = []
        params   = {}
        saw_last = [False]

        def generator():
            yield dut.tx.mac_address.eq(mac_address)
            yield dut.tx.transaction_id.eq(transaction_id)
            yield dut.tx.type.eq(packet_type)
            yield dut.tx.offered_ip_address.eq(offered_ip_address)
            yield dut.tx.server_ip_address.eq(server_ip_address)
            yield dut.udp_port.sink.ready.eq(1)
            yield
            yield dut.tx.start.eq(1)
            yield
            yield dut.tx.start.eq(0)

            for _ in range(256):
                if (yield dut.udp_port.sink.valid):
                    if not packet:
                        params["src_port"]   = (yield dut.udp_port.sink.src_port)
                        params["dst_port"]   = (yield dut.udp_port.sink.dst_port)
                        params["ip_address"] = (yield dut.udp_port.sink.ip_address)
                        params["length"]     = (yield dut.udp_port.sink.length)
                    packet.extend(split_word((yield dut.udp_port.sink.data)))
                    if (yield dut.udp_port.sink.last):
                        params["last_be"] = (yield dut.udp_port.sink.last_be)
                        saw_last[0] = True
                        break
                yield

        run_simulation(dut, generator())
        self.assertTrue(saw_last[0])
        return packet, params

    def test_discover_packet_basic_fields(self):
        packet, params = self.capture_packet(DHCP_TX_DISCOVER)

        self.assertEqual(params["src_port"], DHCP_CLIENT_PORT)
        self.assertEqual(params["dst_port"], DHCP_SERVER_PORT)
        self.assertEqual(params["ip_address"], convert_ip("255.255.255.255"))
        self.assertEqual(params["length"], DHCP_FIXED_DISCOVER_LENGTH)
        self.assertEqual(params["last_be"], 0b1000)

        self.assertEqual(packet[0:4], [0x01, 0x01, 0x06, 0x00])
        self.assertEqual(packet[4:8], split_word(transaction_id))
        self.assertEqual(packet[28:34], split_be(mac_address, 6))
        self.assertEqual(packet[236:240], [0x63, 0x82, 0x53, 0x63])

        options = parse_options(packet[240:])
        self.assertEqual(options[DHCP_OPTTYP_MESSAGE_TYPE], [DHCP_OPTVAL_MESSAGE_TYPE_DISCOVER])
        self.assertEqual(options[DHCP_OPTTYP_CLIENT_IDENTIFIER], split_be(mac_address, 6))

    def test_request_packet_includes_requested_ip_and_server_identifier(self):
        packet, params = self.capture_packet(DHCP_TX_REQUEST)

        self.assertEqual(params["length"], DHCP_FIXED_REQUEST_LENGTH)

        options = parse_options(packet[240:])
        self.assertEqual(options[DHCP_OPTTYP_MESSAGE_TYPE], [DHCP_OPTVAL_MESSAGE_TYPE_REQUEST])
        self.assertEqual(merge_be(options[DHCP_OPTTYP_REQ_IP_ADDRESS]), offered_ip_address)
        self.assertEqual(merge_be(options[DHCP_OPTTYP_SRV_IP_ADDRESS]), server_ip_address)

if __name__ == "__main__":
    unittest.main()
