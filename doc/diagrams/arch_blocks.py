#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Core architecture: frontends, UDP, IP with ARP/ICMP, MAC, PHY, external PHY or SFP.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 260)
    d.add(Container(120, 20, 415, 225, title="LiteEthUDPIPCore", title_pos="bl"))
    fe   = d.add(Box(10,  70, 90, 70, label="Frontends\nuser logic"))
    udp  = d.add(Box(140, 70, 80, 70, label="UDP", fill=COLOR_ACCENT))
    ip   = d.add(Box(250, 70, 80, 70, label="IPv4", fill=COLOR_ACCENT))
    icmp = d.add(Box(240, 165, 60, 46, label="ICMP", fill=COLOR_ACCENT_LIGHT))
    arp  = d.add(Box(345, 165, 70, 46, label="ARP", fill=COLOR_ACCENT_LIGHT))
    mac  = d.add(Box(430, 70, 90, 70, label="MAC", fill=COLOR_ACCENT))
    phy  = d.add(Box(560, 70, 70, 70, label="PHY", fill=COLOR_ACCENT_LIGHT))
    ext  = d.add(Box(660, 70, 90, 70, label="External\nPHY or SFP", fill=COLOR_HARDWARE))
    d.add(Arrow((fe, "r"), (udp, "l"), kind="data", heads="both"))
    d.add(Arrow((udp, "r"), (ip, "l"), kind="accent", heads="both"))
    d.add(Arrow((ip, "r"), (mac, "l"), kind="accent", heads="both"))
    d.add(Arrow((mac, "r"), (phy, "l"), kind="data", heads="both"))
    d.add(Arrow((phy, "r"), (ext, "l"), kind="data", heads="both"))
    d.add(Arrow((270, icmp.y), (270, ip.y + ip.h), kind="data", heads="both"))
    d.add(Arrow((arp, "r"), (475, mac.y + mac.h), via=[(475, arp.cy)], kind="data", heads="both"))
    d.add(Arrow((arp, "l"), (318, ip.y + ip.h), via=[(318, arp.cy)], kind="control",
        label="lookup", label_pos=(326, 153), label_anchor="start"))
    return d
