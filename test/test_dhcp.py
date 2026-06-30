#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen.sim import *
from litex.soc.interconnect import stream
from test.stream_helpers import Packet, PacketStreamer

from liteeth.common import *
from liteeth.core.dhcp import *

# Constants ----------------------------------------------------------------------------------------

mac_address        = 0x10e2d5000001
transaction_id     = 0x12345678
offered_ip_address = convert_ip("192.168.1.100")
server_ip_address  = convert_ip("192.168.1.1")
lease_time         = 3600

# Helpers ------------------------------------------------------------------------------------------

def split_word(word):
    return [(word >> (8*i)) & 0xff for i in range(4)]

def merge_word(data):
    value = 0
    for i, byte in enumerate(data):
        value |= byte << (8*i)
    return value

def words_from_bytes(data):
    words = []
    for offset in range(0, len(data), 4):
        word_data = data[offset:offset + 4]
        while len(word_data) < 4:
            word_data.append(0)
        words.append(merge_word(word_data))
    return words

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

def last_be_for_length(length):
    remainder = length % 4
    if remainder == 0:
        return 0b1000
    return 1 << (remainder - 1)

def build_dhcp_response(
    message_type      = DHCP_OPTVAL_MESSAGE_TYPE_OFFER,
    options           = None,
    yiaddr            = offered_ip_address,
    siaddr            = server_ip_address,
    xid               = transaction_id,
    client_mac_address = mac_address,
):
    if options is None:
        options = [
            DHCP_OPTTYP_MESSAGE_TYPE, 0x01, message_type,
            DHCP_OPTTYP_END,
        ]

    packet = []
    packet += [0x02, 0x01, 0x06, 0x00] # BOOTP reply, Ethernet, 6-byte MAC, no hops.
    packet += split_word(xid)
    packet += [0x00, 0x00, 0x00, 0x00] # Seconds + flags.
    packet += split_be(0, 4)            # ciaddr.
    packet += split_be(yiaddr, 4)       # yiaddr.
    packet += split_be(siaddr, 4)       # siaddr.
    packet += split_be(0, 4)            # giaddr.
    packet += split_be(client_mac_address, 6)
    packet += [0x00]*(16 - 6)           # chaddr padding.
    packet += [0x00]*DHCP_SERVER_NAME_LENGTH
    packet += [0x00]*DHCP_BOOT_FILE_NAME_LENGTH
    packet += [0x63, 0x82, 0x53, 0x63]
    packet += options
    return packet

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

    def test_discover_broadcast_flag_and_parameter_request_list(self):
        packet, _ = self.capture_packet(DHCP_TX_DISCOVER)

        self.assertEqual(packet[8:12], [0x00, 0x00, 0x80, 0x00])

        options = parse_options(packet[240:])
        self.assertEqual(
            options[DHCP_OPTTYP_PARAM_REQUEST_LIST],
            [0x01, 0x03], # Subnet Mask, Router.
        )

    def test_request_packet_includes_requested_ip_and_server_identifier(self):
        packet, params = self.capture_packet(DHCP_TX_REQUEST)

        self.assertEqual(params["length"], DHCP_FIXED_REQUEST_LENGTH)

        options = parse_options(packet[240:])
        self.assertEqual(options[DHCP_OPTTYP_MESSAGE_TYPE], [DHCP_OPTVAL_MESSAGE_TYPE_REQUEST])
        self.assertEqual(merge_be(options[DHCP_OPTTYP_REQ_IP_ADDRESS]), offered_ip_address)
        self.assertEqual(merge_be(options[DHCP_OPTTYP_SRV_IP_ADDRESS]), server_ip_address)

# Test DHCP RX -------------------------------------------------------------------------------------

class TestDHCPRX(unittest.TestCase):
    def receive_packet(self, packet, src_port=DHCP_SERVER_PORT, dst_port=DHCP_CLIENT_PORT, length=None):
        class RXDUT(LiteXModule):
            def __init__(self, last_be):
                self.streamer        = PacketStreamer(eth_udp_user_description(32), last_be=last_be)
                self.udp_port        = udp_port = type("UDPPort", (), {})()
                self.udp_port.sink   = stream.Endpoint(eth_udp_user_description(32))
                self.udp_port.source = stream.Endpoint(eth_udp_user_description(32))

                self.rx = LiteEthDHCPRX(udp_port)
                self.comb += self.streamer.source.connect(udp_port.source)

        length = len(packet) if length is None else length
        dut    = RXDUT(last_be_for_length(length))
        result = {"present": False}

        words = words_from_bytes(packet)

        def generator():
            yield dut.rx.mac_address.eq(mac_address)
            yield dut.rx.transaction_id.eq(transaction_id)
            yield dut.streamer.source.src_port.eq(src_port)
            yield dut.streamer.source.dst_port.eq(dst_port)
            yield dut.streamer.source.length.eq(length)
            yield dut.streamer.source.ip_address.eq(convert_ip("255.255.255.255"))

            dut.streamer.send(Packet(words))

            for _ in range(512):
                if (yield dut.rx.present):
                    result.update(
                        present            = True,
                        error              = (yield dut.rx.error),
                        type               = (yield dut.rx.type),
                        offered_ip_address = (yield dut.rx.offered_ip_address),
                        server_ip_address  = (yield dut.rx.server_ip_address),
                        lease_time         = (yield dut.rx.lease_time),
                    )
                    yield dut.rx.ack.eq(1)
                    yield
                    break
                yield

        run_simulation(dut, [generator(), dut.streamer.generator()])
        self.assertTrue(result["present"])
        return result

    def test_offer_with_full_bootp_header(self):
        packet = build_dhcp_response(options=[
            DHCP_OPTTYP_MESSAGE_TYPE, 0x01, DHCP_OPTVAL_MESSAGE_TYPE_OFFER,
            DHCP_OPTTYP_END, 0x00, 0x00, 0x00, 0x00,
        ])
        result = self.receive_packet(packet)

        self.assertEqual(result["error"], 0)
        self.assertEqual(result["type"], DHCP_RX_OFFER)
        self.assertEqual(result["offered_ip_address"], offered_ip_address)
        self.assertEqual(result["server_ip_address"], server_ip_address)

    def test_offer_parses_server_identifier_option(self):
        packet = build_dhcp_response(
            siaddr  = 0,
            options = [
                0x00, # Pad before real options.
                DHCP_OPTTYP_SRV_IP_ADDRESS, 0x04, *split_be(server_ip_address, 4),
                DHCP_OPTTYP_MESSAGE_TYPE, 0x01, DHCP_OPTVAL_MESSAGE_TYPE_OFFER,
                DHCP_OPTTYP_END,
            ],
        )
        result = self.receive_packet(packet)

        self.assertEqual(result["error"], 0)
        self.assertEqual(result["type"], DHCP_RX_OFFER)
        self.assertEqual(result["server_ip_address"], server_ip_address)

    def test_ack_allows_message_type_after_other_options(self):
        packet = build_dhcp_response(
            message_type = DHCP_OPTVAL_MESSAGE_TYPE_ACK,
            siaddr       = 0,
            options      = [
                DHCP_OPTTYP_SRV_IP_ADDRESS, 0x04, *split_be(server_ip_address, 4),
                0x00,
                DHCP_OPTTYP_MESSAGE_TYPE, 0x01, DHCP_OPTVAL_MESSAGE_TYPE_ACK,
                DHCP_OPTTYP_END,
            ],
        )
        result = self.receive_packet(packet)

        self.assertEqual(result["error"], 0)
        self.assertEqual(result["type"], DHCP_RX_ACK)
        self.assertEqual(result["server_ip_address"], server_ip_address)

    def test_ack_parses_lease_time_option(self):
        packet = build_dhcp_response(
            message_type = DHCP_OPTVAL_MESSAGE_TYPE_ACK,
            options      = [
                DHCP_OPTTYP_SRV_IP_ADDRESS, 0x04, *split_be(server_ip_address, 4),
                DHCP_OPTTYP_LEASE_TIME,     0x04, *split_be(lease_time, 4),
                DHCP_OPTTYP_MESSAGE_TYPE,   0x01, DHCP_OPTVAL_MESSAGE_TYPE_ACK,
                DHCP_OPTTYP_END,
            ],
        )
        result = self.receive_packet(packet)

        self.assertEqual(result["error"], 0)
        self.assertEqual(result["type"], DHCP_RX_ACK)
        self.assertEqual(result["lease_time"], lease_time)

    def test_bad_lease_time_length_is_rejected(self):
        packet = build_dhcp_response(
            message_type = DHCP_OPTVAL_MESSAGE_TYPE_ACK,
            options      = [
                DHCP_OPTTYP_MESSAGE_TYPE, 0x01, DHCP_OPTVAL_MESSAGE_TYPE_ACK,
                DHCP_OPTTYP_LEASE_TIME,   0x03, 0x00, 0x0e, 0x10,
                DHCP_OPTTYP_END,
            ],
        )
        result = self.receive_packet(packet)

        self.assertEqual(result["error"], 1)

# Test DHCP Core -----------------------------------------------------------------------------------

class TestDHCPCore(unittest.TestCase):
    def test_handshake_with_simulated_server(self):
        class CoreDUT(LiteXModule):
            def __init__(self):
                self.streamer        = PacketStreamer(eth_udp_user_description(32), last_be=0b1000)
                self.udp_port        = udp_port = type("UDPPort", (), {})()
                self.udp_port.sink   = stream.Endpoint(eth_udp_user_description(32))
                self.udp_port.source = stream.Endpoint(eth_udp_user_description(32))

                self.dhcp = LiteEthDHCP(udp_port, sys_clk_freq=100000, timeout=1)
                self.comb += self.streamer.source.connect(udp_port.source)

        dut     = CoreDUT()
        results = {
            "discover_seen": False,
            "request_seen":  False,
            "ack_sent":      False,
            "done":          False,
            "timeout":       False,
            "ip_address":    0,
            "lease_time":     0,
        }

        def make_server_response(message_type, xid):
            return build_dhcp_response(
                message_type = message_type,
                siaddr       = 0,
                xid          = xid,
                options      = [
                    DHCP_OPTTYP_SRV_IP_ADDRESS, 0x04, *split_be(server_ip_address, 4),
                    DHCP_OPTTYP_MESSAGE_TYPE, 0x01, message_type,
                    DHCP_OPTTYP_LEASE_TIME, 0x04, *split_be(lease_time, 4),
                    DHCP_OPTTYP_END,
                ],
            )

        def send_server_packet(packet):
            yield dut.streamer.source.src_port.eq(DHCP_SERVER_PORT)
            yield dut.streamer.source.dst_port.eq(DHCP_CLIENT_PORT)
            yield dut.streamer.source.length.eq(len(packet))
            yield dut.streamer.source.ip_address.eq(convert_ip("255.255.255.255"))
            dut.streamer.send(Packet(words_from_bytes(packet)))

        def generator():
            yield dut.dhcp.mac_address.eq(mac_address)
            yield dut.udp_port.sink.ready.eq(1)
            yield dut.dhcp.start.eq(1)
            yield
            yield dut.dhcp.start.eq(0)

            tx_packet = []
            tx_params = {}
            tx_count  = 0

            for _ in range(2048):
                if (yield dut.udp_port.sink.valid):
                    if not tx_packet:
                        tx_params["length"] = (yield dut.udp_port.sink.length)
                    tx_packet.extend(split_word((yield dut.udp_port.sink.data)))
                    if (yield dut.udp_port.sink.last):
                        tx_count += 1
                        packet  = tx_packet[:tx_params["length"]]
                        options = parse_options(packet[240:])
                        xid     = merge_word(packet[4:8])

                        if tx_count == 1:
                            self.assertEqual(options[DHCP_OPTTYP_MESSAGE_TYPE],
                                [DHCP_OPTVAL_MESSAGE_TYPE_DISCOVER])
                            results["discover_seen"] = True
                            yield from send_server_packet(
                                make_server_response(DHCP_OPTVAL_MESSAGE_TYPE_OFFER, xid))

                        elif tx_count == 2:
                            self.assertEqual(options[DHCP_OPTTYP_MESSAGE_TYPE],
                                [DHCP_OPTVAL_MESSAGE_TYPE_REQUEST])
                            self.assertEqual(
                                merge_be(options[DHCP_OPTTYP_REQ_IP_ADDRESS]),
                                offered_ip_address)
                            self.assertEqual(
                                merge_be(options[DHCP_OPTTYP_SRV_IP_ADDRESS]),
                                server_ip_address)
                            results["request_seen"] = True
                            yield from send_server_packet(
                                make_server_response(DHCP_OPTVAL_MESSAGE_TYPE_ACK, xid))
                            results["ack_sent"] = True

                        tx_packet = []
                        tx_params = {}

                results["timeout"] = bool((yield dut.dhcp.timeout))
                if results["ack_sent"] and (yield dut.dhcp.done):
                    results["done"]       = True
                    results["ip_address"] = (yield dut.dhcp.ip_address)
                    results["lease_time"] = (yield dut.dhcp.lease_time)
                    break
                yield

        run_simulation(dut, [generator(), dut.streamer.generator()])

        self.assertTrue(results["discover_seen"])
        self.assertTrue(results["request_seen"])
        self.assertTrue(results["ack_sent"])
        self.assertTrue(results["done"])
        self.assertFalse(results["timeout"])
        self.assertEqual(results["ip_address"], offered_ip_address)
        self.assertEqual(results["lease_time"], lease_time)

    def test_ignores_rx_errors_while_waiting_for_offer(self):
        bad_packet = build_dhcp_response(
            xid     = 2,
            options = [
                DHCP_OPTTYP_MESSAGE_TYPE, 0x01, DHCP_OPTVAL_MESSAGE_TYPE_OFFER,
                DHCP_OPTTYP_END, 0x00, 0x00, 0x00, 0x00,
            ],
        )
        bad_words = words_from_bytes(bad_packet)

        class CoreDUT(LiteXModule):
            def __init__(self):
                self.streamer        = PacketStreamer(eth_udp_user_description(32),
                    last_be=last_be_for_length(len(bad_packet)))
                self.udp_port        = udp_port = type("UDPPort", (), {})()
                self.udp_port.sink   = stream.Endpoint(eth_udp_user_description(32))
                self.udp_port.source = stream.Endpoint(eth_udp_user_description(32))

                self.dhcp = LiteEthDHCP(udp_port, sys_clk_freq=100000, timeout=1)
                self.comb += self.streamer.source.connect(udp_port.source)

        dut     = CoreDUT()
        results = {
            "discover_seen": False,
            "rx_error_seen": False,
            "request_seen":  False,
        }

        def generator():
            yield dut.dhcp.mac_address.eq(mac_address)
            yield dut.udp_port.sink.ready.eq(1)
            yield dut.streamer.source.src_port.eq(DHCP_SERVER_PORT)
            yield dut.streamer.source.dst_port.eq(DHCP_CLIENT_PORT)
            yield dut.streamer.source.length.eq(len(bad_packet))

            yield dut.dhcp.start.eq(1)
            yield
            yield dut.dhcp.start.eq(0)

            tx_packets = 0
            send_delay = None
            bad_sent   = False

            for _ in range(512):
                if (yield dut.udp_port.sink.valid) and (yield dut.udp_port.sink.last):
                    tx_packets += 1
                    if tx_packets == 1:
                        results["discover_seen"] = True
                        send_delay = 4
                    elif bad_sent:
                        results["request_seen"] = True

                if bad_sent and (yield dut.udp_port.sink.valid):
                    results["request_seen"] = True

                if send_delay is not None:
                    if send_delay == 0:
                        dut.streamer.send(Packet(bad_words))
                        bad_sent = True
                        send_delay = None
                    else:
                        send_delay -= 1

                if (yield dut.dhcp.rx.present) and (yield dut.dhcp.rx.error):
                    results["rx_error_seen"] = True

                yield

        run_simulation(dut, [generator(), dut.streamer.generator()])

        self.assertTrue(results["discover_seen"])
        self.assertTrue(results["rx_error_seen"])
        self.assertFalse(results["request_seen"])

# Test DHCP Signals --------------------------------------------------------------------------------

class TestDHCPSignals(unittest.TestCase):
    def test_ipv4_signal_widths(self):
        dut = DUT()

        self.assertEqual(len(dut.tx.offered_ip_address), 32)
        self.assertEqual(len(dut.rx.offered_ip_address), 32)
        self.assertEqual(len(dut.rx.lease_time), 32)

        class CoreDUT(LiteXModule):
            def __init__(self):
                self.udp_port        = udp_port = type("UDPPort", (), {})()
                self.udp_port.sink   = stream.Endpoint(eth_udp_user_description(32))
                self.udp_port.source = stream.Endpoint(eth_udp_user_description(32))
                self.dhcp            = LiteEthDHCP(udp_port, sys_clk_freq=100000)

        core_dut = CoreDUT()
        self.assertEqual(len(core_dut.dhcp.ip_address), 32)
        self.assertEqual(len(core_dut.dhcp.lease_time), 32)

if __name__ == "__main__":
    unittest.main()
