#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Protocol layers: UDP and IP TX/RX, ARP TX/RX and table, on top of the MAC crossbar.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 330)
    udptx = d.add(Box(40,  20,  180, 50, label="UDP TX", fill=COLOR_ACCENT))
    iptx  = d.add(Box(40,  100, 180, 50, label="IP TX", fill=COLOR_ACCENT))
    table = d.add(Box(290, 100, 180, 50, label="ARP table", fill=COLOR_ACCENT_LIGHT))
    arptx = d.add(Box(290, 180, 180, 50, label="ARP TX", fill=COLOR_ACCENT_LIGHT))
    arprx = d.add(Box(520, 180, 100, 50, label="ARP RX", fill=COLOR_ACCENT_LIGHT))
    iprx  = d.add(Box(640, 100, 100, 50, label="IP RX", fill=COLOR_ACCENT))
    udprx = d.add(Box(640, 20,  100, 50, label="UDP RX", fill=COLOR_ACCENT))
    mac   = d.add(Box(40,  270, 700, 46, label="MAC crossbar"))
    d.add(Arrow((udptx, "b"), (iptx, "t"), kind="accent"))
    d.add(Arrow((iptx, "b"), (130, mac.y), kind="accent"))
    d.add(Arrow((iptx, "r"), (table, "l"), kind="control", heads="both", label="lookup",
        label_pos=(255, 117)))
    d.add(Arrow((table, "b"), (arptx, "t"), kind="control", label="request, reply",
        label_pos=(388, 168), label_anchor="start"))
    d.add(Arrow((arptx, "b"), (380, mac.y), kind="data"))
    d.add(Arrow((570, mac.y), (arprx, "b"), kind="data"))
    d.add(Arrow((arprx, "l"), (table, "r"), via=[(495, arprx.cy), (495, table.cy)], kind="control"))
    d.add(Arrow((690, mac.y), (iprx, "b"), kind="accent"))
    d.add(Arrow((iprx, "t"), (udprx, "b"), kind="accent"))
    return d
