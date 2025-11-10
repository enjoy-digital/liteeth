#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from liteeth.common import *

from test.model.dumps import *
from test.model.mac import MACPacket
from test.model.arp import ARPPacket
from test.model.ip import IPPacket
from test.model.icmp import ICMPPacket
from test.model.udp import UDPPacket
from litex.tools.remote.etherbone import *
from test.model.etherbone import Etherbone

from litex.gen.sim import *

# Test Model ---------------------------------------------------------------------------------------

class TestModel(unittest.TestCase):
    def test_mac(self, debug=False):
        errors = 0
        packet = MACPacket(arp_request)
        packet.decode_remove_header()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_request_infos)
        packet.encode_header()
        packet.decode_remove_header()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_request_infos)

        if debug:
            print(packet)
        packet = MACPacket(arp_reply)
        packet.decode_remove_header()
        errors += verify_packet(packet, arp_reply_infos)
        packet.encode_header()
        packet.decode_remove_header()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_reply_infos)

        self.assertEqual(errors, 0)

    def test_arp(self, debug=False):
        errors = 0
        # ARP Request.
        packet = MACPacket(arp_request)
        packet.decode_remove_header()
        packet = ARPPacket(packet)
        # Check Decoding.
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_request_infos)
        # Check Encoding.
        packet.encode()
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_request_infos)

        # ARP Reply.
        packet = MACPacket(arp_reply)
        packet.decode_remove_header()
        packet = ARPPacket(packet)
        # Check Decoding.
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_reply_infos)
        # Check Encoding.
        packet.encode()
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, arp_reply_infos)

        self.assertEqual(errors, 0)

    def test_ip(self, debug=False):
        errors = 0
        # UDP Packet.
        packet = MACPacket(udp)
        packet.decode_remove_header()
        if debug:
            print(packet)
        packet = IPPacket(packet)
        # Check Decoding.
        errors += not packet.check_checksum()
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, {})
        # Check Encoding.
        packet.encode()
        packet.insert_checksum()
        errors += not packet.check_checksum()
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, {})

        self.assertEqual(errors, 0)

    def test_icmp(self, debug=False):
        errors = 0
        # ICMP Packet.
        packet = MACPacket(ping_request)
        packet.decode_remove_header()
        if debug:
            print(packet)
        packet = IPPacket(packet)
        packet.decode()
        if debug:
            print(packet)
        packet = ICMPPacket(packet)
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, ping_request_infos)
        packet.encode()
        packet.decode()
        if debug:
            print(packet)
        errors += verify_packet(packet, ping_request_infos)

        self.assertEqual(errors, 0)

    def test_udp(self, debug=False):
        errors = 0
        # UDP Packet.
        packet = MACPacket(udp)
        packet.decode_remove_header()
        if debug:
            print(packet)
        packet = IPPacket(packet)
        packet.decode()
        if debug:
            print(packet)
        packet = UDPPacket(packet)
        packet.decode()
        if debug:
            print(packet)
        if packet.length != (len(packet)+udp_header.length):
            errors += 1
        errors += verify_packet(packet, udp_infos)
        packet.encode()
        packet.decode()
        if debug:
            print(packet)
        if packet.length != (len(packet)+udp_header.length):
            errors += 1
        errors += verify_packet(packet, udp_infos)

        self.assertEqual(errors, 0)

    def test_etherbone(self, debug=False):
        # Writes/Reads.
        writes = EtherboneWrites(base_addr    =0x1000, datas=[i for i in range(16)])
        reads  = EtherboneReads( base_ret_addr=0x2000, addrs=[i for i in range(16)])

        # Record.
        record = EtherboneRecord()
        record.writes = writes
        record.reads  = reads
        record.wcount = len(writes.get_datas())
        record.rcount = len(reads.get_addrs())

        # Packet.
        packet = EtherbonePacket()
        from copy import deepcopy
        packet.records = [deepcopy(record) for i in range(8)]
        if debug:
            print(packet)
        packet.encode()
        if debug:
            print(packet)

        # Send packet over UDP to check against Wireshark dissector
        #import socket
        #sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        #sock.sendto(bytes(packet), ("192.168.1.1", 20000))

        packet = EtherbonePacket(init=packet)
        packet.encoded = True
        packet.decode()
        if debug:
            print(packet)

        self.assertEqual(0, 0) # FIXME
