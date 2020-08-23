#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from litex.soc.interconnect.packet import Depacketizer, Packetizer

# MAC Packetizer/Depacketizer ----------------------------------------------------------------------

class LiteEthMACDepacketizer(Depacketizer):
    def __init__(self, dw):
        Depacketizer.__init__(self,
            eth_phy_description(dw),
            eth_mac_description(dw),
            mac_header)


class LiteEthMACPacketizer(Packetizer):
    def __init__(self, dw):
        Packetizer.__init__(self,
            eth_mac_description(dw),
            eth_phy_description(dw),
            mac_header)

# MAC Ports ----------------------------------------------------------------------------------------

class LiteEthMACMasterPort:
    def __init__(self, dw):
        self.source = stream.Endpoint(eth_mac_description(dw))
        self.sink   = stream.Endpoint(eth_mac_description(dw))


class LiteEthMACSlavePort:
    def __init__(self, dw):
        self.sink   = stream.Endpoint(eth_mac_description(dw))
        self.source = stream.Endpoint(eth_mac_description(dw))


class LiteEthMACUserPort(LiteEthMACSlavePort):
    def __init__(self, dw):
        LiteEthMACSlavePort.__init__(self, dw)


class LiteEthMACCrossbar(LiteEthCrossbar):
    def __init__(self, dw=8):
        LiteEthCrossbar.__init__(self, LiteEthMACMasterPort, "ethernet_type", dw)

    def get_port(self, ethernet_type, dw=8):
        port = LiteEthMACUserPort(dw)
        if ethernet_type in self.users.keys():
            raise ValueError("Ethernet type {0:#x} already assigned".format(ethernet_type))
        self.users[ethernet_type] = port
        return port
