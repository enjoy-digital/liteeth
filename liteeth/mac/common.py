#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.crossbar import LiteEthCrossbar

from liteeth.packet import Depacketizer, Packetizer

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

# Last Handler -------------------------------------------------------------------------------------

class LiteEthLastHandler(LiteXModule):
    def __init__(self, layout):
        self.sink   =   sink = stream.Endpoint(layout)
        self.source = source = stream.Endpoint(layout)

        # # #

        self.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            sink.connect(source),
            source.last.eq(sink.last_be != 0),
            If(sink.valid & sink.ready,
                # If last Byte but not last packet token.
                If(source.last & ~sink.last,
                    NextState("WAIT-LAST")
                )
            )
        )
        fsm.act("WAIT-LAST",
            # Accept incoming stream until we receive last packet token.
            sink.ready.eq(1),
            If(sink.valid & sink.last,
                NextState("COPY")
            )
        )
