#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import math

from test.stream_helpers import *
from litex.tools.remote.etherbone import *

from liteeth.common import *

from test.model import udp

# Helpers ------------------------------------------------------------------------------------------

def print_etherbone(s):
    print_with_prefix(s, "[ETHERBONE]")


# Etherbone ----------------------------------------------------------------------------------------

class Etherbone(Module):
    def __init__(self, udp, debug=False):
        self.udp   = udp
        self.debug = debug
        self.tx_packets = []
        self.tx_packet  = EtherbonePacket()
        self.rx_packet  = EtherbonePacket()

        udp.set_etherbone_callback(self.callback)

    def send(self, packet, target_ip=0x12345678):
        if not packet.encoded:
            packet.encode()
        if self.debug:
            print_etherbone(">>>>>>>>")
            print_etherbone(packet)
        udp_packet = udp.UDPPacket(packet.bytes)
        udp_packet.src_port = 0x1234 # FIXME
        udp_packet.dst_port = 0x1234 # FIXME
        udp_packet.length   = len(packet.bytes) + udp_header.length
        udp_packet.checksum = 0
        self.udp.send(udp_packet, target_ip=target_ip)

    def receive(self, timeout=None):
        self.rx_packet = EtherbonePacket()
        cycles = 0
        while not self.rx_packet.done:
            if timeout is not None and cycles >= timeout:
                break
            cycles += 1
            yield

    def callback(self, packet):
        packet = EtherbonePacket(init=bytes(packet))
        packet.decode()
        if self.debug:
            print_etherbone("<<<<<<<<")
            print_etherbone(packet)
        self.rx_packet = packet
        self.rx_packet.done = True
        self.process(packet)

    def process(self, packet):
        pass
