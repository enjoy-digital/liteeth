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

# IP Core ------------------------------------------------------------------------------------------

class LiteEthIPCore(Module, AutoCSR):
    def __init__(self, phy, mac_address, ip_address, clk_freq, with_icmp=True, dw=8, with_sys_datapath=False):
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
            mac         = self.mac,
            mac_address = mac_address,
            ip_address  = ip_address,
            arp_table   = self.arp.table,
            dw          = dw,
        )
        # ICMP (Optional).
        # ----------------
        if with_icmp:
            self.submodules.icmp = LiteEthICMP(
                ip         = self.ip,
                ip_address = ip_address,
                dw         = dw,
            )

# UDP IP Core --------------------------------------------------------------------------------------

class LiteEthUDPIPCore(LiteEthIPCore):
    def __init__(self, phy, mac_address, ip_address, clk_freq, with_icmp=True, dw=8, with_sys_datapath=False):
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
            with_sys_datapath = with_sys_datapath,
        )
        # UDP.
        # ----
        self.submodules.udp = LiteEthUDP(
            ip         = self.ip,
            ip_address = ip_address,
            dw         = dw,
        )
