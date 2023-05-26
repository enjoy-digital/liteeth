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

# PHY Source ---------------------------------------------------------------------------------------

class PHYSource(PacketStreamer):
    def __init__(self, dw, assertStall):
        PacketStreamer.__init__(self, eth_phy_description(dw), dw=dw, assertStall=assertStall)

# PHY Sink -----------------------------------------------------------------------------------------

class PHYSink(PacketLogger):
    def __init__(self, dw, assertStall):
        PacketLogger.__init__(self, eth_phy_description(dw), dw=dw, assertStall=assertStall)

# PHY ----------------------------------------------------------------------------------------------

LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_ETHERNET_MPACKET = 274

class PHY(Module):
    def __init__(self, dw, debug=False, pcap_file=None, assertStall=False):
        self.dw    = dw
        self.debug = debug

        self.submodules.phy_source = PHYSource(dw, assertStall)
        self.submodules.phy_sink   = PHYSink(dw, assertStall)

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
                r += f'{d:02x} '
            print_phy(r)

        if self.pcap_file is not None and n_bytes > 0:
            with open(self.pcap_file, 'ab') as f:
                f.write(pack('IIII', self.cc, 0, n_bytes, n_bytes))
                f.write(bytes(datas))

        self.phy_source.send(Packet(datas))

    def receive(self):
        yield from self.phy_sink.receive()
        self.packet = p = self.phy_sink.packet  # Each item is a byte
        if self.debug:
            r = "<<<<<<<<\n"
            r += "length " + str(len(p)) + "\n"
            for d in p:
                r += f'{d:02x} '
            print_phy(r)

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
