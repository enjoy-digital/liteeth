#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from struct import pack
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *

# Helpers ------------------------------------------------------------------------------------------

def print_phy(s):
    print_with_prefix(s, "[PHY]")


def bytes_to_words(bs, width):
    ws = []
    n_words = len(bs) // width
    for i in range(n_words):
        tmp = bs[i * width: (i + 1) * width]
        ws.append(merge_bytes(tmp[::-1]))
    return ws

# PHY Source ---------------------------------------------------------------------------------------

class PHYSource(PacketStreamer):
    def __init__(self, dw):
        PacketStreamer.__init__(self, eth_phy_description(dw))

# PHY Sink -----------------------------------------------------------------------------------------

class PHYSink(PacketLogger):
    def __init__(self, dw):
        PacketLogger.__init__(self, eth_phy_description(dw))

# PHY ----------------------------------------------------------------------------------------------

LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_ETHERNET_MPACKET = 274

class PHY(Module):
    def __init__(self, dw, debug=False, pcap_file=None):
        self.dw    = dw
        self.debug = debug

        self.submodules.phy_source = PHYSource(dw)
        self.submodules.phy_sink   = PHYSink(dw)

        self.source = self.phy_source.source
        self.sink   = self.phy_sink.sink

        self.mac_callback = None

        self.cc = 0
        self.pcap_file = pcap_file
        if pcap_file is not None:
            file_header = pack(
                'IHHiIII',
                0xa1b2c3d4,
                2,
                4,
                0,
                0,
                65535,
                LINKTYPE_ETHERNET_MPACKET
            )
            with open(pcap_file, 'wb') as f:
                f.write(file_header)

    def set_mac_callback(self, callback):
        self.mac_callback = callback

    def send(self, datas):
        n_bytes = len(datas)
        if self.debug:
            r = ">>>>>>>>\n"
            r += "length " + str(n_bytes) + "\n"
            for d in datas:
                r += "{:02x}".format(d)
            print_phy(r)

        if self.pcap_file is not None and n_bytes > 0:
            with open(self.pcap_file, 'ab') as f:
                f.write(pack('IIII', self.cc, 0, n_bytes, n_bytes))
                f.write(bytes(datas))

        packet = Packet(bytes_to_words(datas, self.dw // 8))
        self.phy_source.send(packet, n_bytes)

    def receive(self):
        yield from self.phy_sink.receive()
        p = self.phy_sink.packet  # Each item is a word of width self.dw
        if self.debug:
            r = "<<<<<<<<\n"
            r += "length " + str(len(p)) + "\n"
            for d in p:
                r += f'{d:0{self.dw // 4}x} '
            print_phy(r)

        # Each item is a byte
        self.packet = [b for w in p for b in split_bytes(w, self.dw // 8, "little")]

        if self.pcap_file is not None:
            ll = len(self.packet)  # - 8
            if ll > 0:
                with open(self.pcap_file, 'ab') as f:
                    f.write(pack('IIII', self.cc, 0, ll, ll))
                    f.write(bytes(self.packet))


    @passive
    def generator(self):
        while True:
            yield from self.receive()
            if self.mac_callback is not None:
                self.mac_callback(self.packet)
            self.cc += 1
