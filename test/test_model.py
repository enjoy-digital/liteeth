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

class TestModel(unittest.TestCase):
    def test_mac(self):
        errors = 0
        packet = MACPacket(arp_request)
        packet.decode_remove_header()
        # print(packet)
        errors += verify_packet(packet, arp_request_infos)
        packet.encode_header()
        packet.decode_remove_header()
        # print(packet)
        errors += verify_packet(packet, arp_request_infos)

        # print(packet)
        packet = MACPacket(arp_reply)
        packet.decode_remove_header()
        errors += verify_packet(packet, arp_reply_infos)
        packet.encode_header()
        packet.decode_remove_header()
        # print(packet)
        errors += verify_packet(packet, arp_reply_infos)

        self.assertEqual(errors, 0)

    def test_arp(self):
        errors = 0
        # ARP request
        packet = MACPacket(arp_request)
        packet.decode_remove_header()
        packet = ARPPacket(packet)
        # check decoding
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, arp_request_infos)
        # check encoding
        packet.encode()
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, arp_request_infos)

        # ARP Reply
        packet = MACPacket(arp_reply)
        packet.decode_remove_header()
        packet = ARPPacket(packet)
        # check decoding
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, arp_reply_infos)
        # check encoding
        packet.encode()
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, arp_reply_infos)

        self.assertEqual(errors, 0)

    def test_ip(self):
        errors = 0
        # UDP packet
        packet = MACPacket(udp)
        packet.decode_remove_header()
        # print(packet)
        packet = IPPacket(packet)
        # check decoding
        errors += not packet.check_checksum()
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, {})
        # check encoding
        packet.encode()
        packet.insert_checksum()
        errors += not packet.check_checksum()
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, {})

        self.assertEqual(errors, 0)

    def test_icmp(self):
        errors = 0
        # ICMP packet
        packet = MACPacket(ping_request)
        packet.decode_remove_header()
        # print(packet)
        packet = IPPacket(packet)
        packet.decode()
        # print(packet)
        packet = ICMPPacket(packet)
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, ping_request_infos)
        packet.encode()
        packet.decode()
        # print(packet)
        errors += verify_packet(packet, ping_request_infos)

        self.assertEqual(errors, 0)

    def test_udp(self):
        errors = 0
        # UDP packet
        packet = MACPacket(udp)
        packet.decode_remove_header()
        # print(packet)
        packet = IPPacket(packet)
        packet.decode()
        # print(packet)
        packet = UDPPacket(packet)
        packet.decode()
        # print(packet)
        if packet.length != (len(packet)+udp_header.length):
            errors += 1
        errors += verify_packet(packet, udp_infos)
        packet.encode()
        packet.decode()
        # print(packet)
        if packet.length != (len(packet)+udp_header.length):
            errors += 1
        errors += verify_packet(packet, udp_infos)

        self.assertEqual(errors, 0)

    def test_etherbone(self):
        # Writes/Reads
        writes = EtherboneWrites(base_addr=0x1000, datas=[i for i in range(16)])
        reads = EtherboneReads(base_ret_addr=0x2000, addrs=[i for i in range(16)])

        # Record
        record = EtherboneRecord()
        record.writes = writes
        record.reads = reads
        record.wcount = len(writes.get_datas())
        record.rcount = len(reads.get_addrs())

        # Packet
        packet = EtherbonePacket()
        from copy import deepcopy
        packet.records = [deepcopy(record) for i in range(8)]
        # print(packet)
        packet.encode()
        # print(packet)

        # Send packet over UDP to check against Wireshark dissector
        #import socket
        #sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        #sock.sendto(bytes(packet), ("192.168.1.1", 20000))

        packet = EtherbonePacket(packet)
        packet.decode()
        print(packet)

        self.assertEqual(0, 0) # FIXME
