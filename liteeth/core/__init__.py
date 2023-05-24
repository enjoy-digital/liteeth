#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.mac import LiteEthMAC
from liteeth.core.arp import LiteEthARP
from liteeth.core.ip import LiteEthIP
from liteeth.core.udp import LiteEthUDP
from liteeth.core.icmp import LiteEthICMP
from liteeth.mac.common import LiteEthMACVLANCrossbar, LiteEthMACVLANPacketizer, LiteEthMACVLANDepacketizer

# IP Core ------------------------------------------------------------------------------------------

class LiteEthIPCore(Module, AutoCSR):
    def __init__(self, phy, mac_address, ip_address, clk_freq, dw=8,
        with_icmp         = True,
        with_ip_broadcast = True,
        with_sys_datapath = False):
        # Parameters.
        # -----------
        ip_address = convert_ip(ip_address)

        # MAC.
        # ----
        self.submodules.mac = LiteEthMAC(
            phy       = phy,
            dw        = dw,
            interface = "crossbar",
            with_preamble_crc = True,
            with_sys_datapath = with_sys_datapath,
        )

        # ARP.
        # ----
        self.submodules.arp = LiteEthARP(
            mac         = self.mac,
            mac_address = mac_address,
            ip_address  = ip_address,
            clk_freq    = clk_freq,
            dw          = dw,
        )

        # IP.
        # ---
        self.submodules.ip  = LiteEthIP(
            mac            = self.mac,
            mac_address    = mac_address,
            ip_address     = ip_address,
            arp_table      = self.arp.table,
            with_broadcast = with_ip_broadcast,
            dw             = dw,
        )
        # ICMP (Optional).
        # ----------------
        if with_icmp:
            self.submodules.icmp = LiteEthICMP(
                ip         = self.ip,
                ip_address = ip_address,
                dw         = dw,
            )

# VLAN UDP IP Core ---------------------------------------------------------------------------------

class LiteEthVLANUDPIPCore(Module, AutoCSR):
    def __init__(self, phy, mac_address, ip_address, clk_freq,
                 with_icmp=True,
                 with_ip_broadcast = True,
                 dw=8):
        self.mac_address = mac_address
        self.with_icmp = with_icmp
        self.with_ip_broadcast = with_ip_broadcast
        self.clk_freq = clk_freq
        self.dw = dw
        ip_address = convert_ip(ip_address)
        self.submodules.mac = LiteEthMAC(phy, dw, interface="crossbar", with_preamble_crc=True)

        self.submodules.arp = LiteEthARP(self.mac, mac_address, ip_address, clk_freq, dw=dw)
        self.submodules.ip  = LiteEthIP(self.mac, mac_address, ip_address, self.arp.table,
                                        with_broadcast=self.with_ip_broadcast,
                                        dw=dw)

        if with_icmp:
            self.submodules.icmp = LiteEthICMP(self.ip, ip_address, dw=dw)

        self.submodules.udp = LiteEthUDP(self.ip, ip_address, dw=dw)

        vlan_mac_port = self.mac.crossbar.get_port(ethernet_8021q_tpid, dw=dw)

        self.submodules.crossbar     = LiteEthMACVLANCrossbar(dw)
        self.submodules.packetizer   = LiteEthMACVLANPacketizer(dw)
        self.submodules.depacketizer = LiteEthMACVLANDepacketizer(dw)

        self.comb += [
            vlan_mac_port.sink.ethernet_type.eq(ethernet_8021q_tpid),
            self.crossbar.master.source.connect(self.packetizer.sink),
            self.packetizer.source.target_mac.eq(self.packetizer.sink.target_mac),
            self.packetizer.source.sender_mac.eq(self.packetizer.sink.sender_mac),
            self.packetizer.source.connect(vlan_mac_port.sink, omit={'ethernet_type'}),
            vlan_mac_port.source.connect(self.depacketizer.sink),
            self.depacketizer.source.connect(self.crossbar.master.sink),
        ]

    def add_vlan(self, vlan_ip="192.168.3.50", vlan_id=2001):
        vlan_ip_address = convert_ip(vlan_ip)
        arp = LiteEthARP(self, self.mac_address, vlan_ip_address,
                         self.clk_freq, dw=self.dw, vlan_id=vlan_id)
        setattr(self.submodules, f"vlan_{vlan_id}_arp", arp)
        ip  = LiteEthIP(self, self.mac_address, vlan_ip_address,
                        arp.table, dw=self.dw, with_broadcast=self.with_ip_broadcast, vlan_id=vlan_id)
        setattr(self.submodules, f"vlan_{vlan_id}_ip", ip)
        if self.with_icmp:
            icmp = LiteEthICMP(ip, vlan_ip_address, dw=self.dw)
            setattr(self.submodules, f"vlan_{vlan_id}_ip", icmp)

        udp = LiteEthUDP(ip, vlan_ip_address, dw=self.dw)
        setattr(self.submodules, f"vlan_{vlan_id}_udp", udp)
        return udp

# UDP IP Core --------------------------------------------------------------------------------------

class LiteEthUDPIPCore(LiteEthIPCore):
    def __init__(self, phy, mac_address, ip_address, clk_freq, dw=8,
        with_icmp         = True,
        with_ip_broadcast = True,
        with_sys_datapath = False):
        # Parameters.
        # -----------
        ip_address = convert_ip(ip_address)

        # Core: MAC + ARP + IP + (ICMP).
        # ------------------------------
        LiteEthIPCore.__init__(self,
            phy         = phy,
            mac_address = mac_address,
            ip_address  = ip_address,
            clk_freq    = clk_freq,
            with_icmp   = with_icmp,
            dw          = dw,
            with_ip_broadcast = with_ip_broadcast,
            with_sys_datapath = with_sys_datapath,
        )
        # UDP.
        # ----
        self.submodules.udp = LiteEthUDP(
            ip         = self.ip,
            ip_address = ip_address,
            dw         = dw,
        )
