# This file is Copyright (c) 2015-2017 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import math
import copy

from litex.soc.interconnect.stream_sim import *
from litex.tools.remote.etherbone import *

from liteeth.common import *

from test.model import udp


def print_etherbone(s):
    print_with_prefix(s, "[ETHERBONE]")


# Etherbone model
class Etherbone(Module):
    def __init__(self, udp, debug=False):
        self.udp = udp
        self.debug = debug
        self.tx_packets = []
        self.tx_packet = EtherbonePacket()
        self.rx_packet = EtherbonePacket()

        udp.set_etherbone_callback(self.callback)

    def send(self, packet):
        packet.encode()
        if self.debug:
            print_etherbone(">>>>>>>>")
            print_etherbone(packet)
        udp_packet = udp.UDPPacket(packet)
        udp_packet.src_port = 0x1234  # XXX
        udp_packet.dst_port = 20000  # XXX
        udp_packet.length = len(packet)
        udp_packet.checksum = 0
        self.udp.send(udp_packet)

    def receive(self):
        self.rx_packet = EtherbonePacket()
        while not self.rx_packet.done:
            yield

    def callback(self, packet):
        packet = EtherbonePacket(packet)
        packet.decode()
        if self.debug:
            print_etherbone("<<<<<<<<")
            print_etherbone(packet)
        self.rx_packet = packet
        self.rx_packet.done = True
        self.process(packet)

    def process(self, packet):
        pass

if __name__ == "__main__":
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
    packet.records = [deepcopy(record) for i in range(8)]
    # print(packet)
    packet.encode()
    # print(packet)

    # Send packet over UDP to check against Wireshark dissector
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(bytes(packet), ("192.168.1.1", 20000))

    packet = EtherbonePacket(packet)
    packet.decode()
    print(packet)
