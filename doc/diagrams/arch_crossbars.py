#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Crossbars: MAC by EtherType, IP by protocol, UDP by port, each user taking one port.

from arch_helpers import *

def build():
    d = Diagram(CANVAS_WIDTH, 420)
    w, h = 170, 46
    macx = d.add(Box(30, 20,  w, h, label="MAC crossbar", fill=COLOR_ACCENT))
    ipx  = d.add(Box(30, 140, w, h, label="IP crossbar", fill=COLOR_ACCENT))
    udpx = d.add(Box(30, 260, w, h, label="UDP crossbar", fill=COLOR_ACCENT))
    arp  = d.add(Box(400, 20,  w, h, label="ARP", fill=COLOR_ACCENT_LIGHT))
    icmp = d.add(Box(400, 140, w, h, label="ICMP", fill=COLOR_ACCENT_LIGHT))
    eb   = d.add(Box(400, 230, w, h, label="Etherbone"))
    st   = d.add(Box(400, 290, w, h, label="UDP streamer"))
    raw  = d.add(Box(400, 350, w, h, label="Raw port"))
    d.add(Arrow((macx, "r"), (arp, "l"), kind="data", heads="both", label="EtherType 0x0806",
        label_pos=(300, macx.cy - 12)))
    d.add(Arrow((macx, "b"), (ipx, "t"), kind="data", heads="both", label="EtherType 0x0800",
        label_pos=(125, 103), label_anchor="start"))
    d.add(Arrow((ipx, "r"), (icmp, "l"), kind="data", heads="both", label="protocol 1",
        label_pos=(300, ipx.cy - 12)))
    d.add(Arrow((ipx, "b"), (udpx, "t"), kind="data", heads="both", label="protocol 17",
        label_pos=(125, 223), label_anchor="start"))
    d.add(Arrow((udpx, "r"), (300, udpx.cy), kind="bus"))
    d.add(Bus([(300, eb.cy), (300, raw.cy)], kind="data"))
    for box, port in ((eb, "port 1234"), (st, "port 6000"), (raw, "port N")):
        d.add(Arrow((300, box.cy), (box, "l"), kind="data", heads="both", label=port,
            label_pos=(350, box.cy - 12)))
    return d

