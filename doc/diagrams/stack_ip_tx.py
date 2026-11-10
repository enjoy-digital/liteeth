#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# IP TX: buffer, header checksum, packetizer, destination state machine with ARP lookup, send or drop.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 290)
    buf  = d.add(Box(20,  110, 110, 56, label="Buffer"))
    csum = d.add(Box(170, 20,  140, 50, label="Header\nchecksum", fill=COLOR_ACCENT_LIGHT))
    pkt  = d.add(Box(170, 110, 140, 56, label="Packetizer", fill=COLOR_ACCENT_LIGHT))
    gate = d.add(Box(360, 110, 130, 56, label="Send or drop", fill=COLOR_ACCENT))
    mac  = d.add(Box(540, 110, 200, 56, label="MAC crossbar"))
    fsm  = d.add(Box(360, 215, 130, 56, label="Destination\nselection", fill=COLOR_ACCENT))
    arp  = d.add(Box(540, 215, 200, 56, label="ARP table", fill=COLOR_ACCENT_LIGHT))
    d.add(Arrow((0, buf.cy), (buf, "l"), kind="accent"))
    d.add(Arrow((buf, "r"), (pkt, "l"), kind="accent"))
    d.add(Arrow((pkt, "r"), (gate, "l"), kind="accent"))
    d.add(Arrow((gate, "r"), (mac, "l"), kind="accent"))
    d.add(Arrow((csum, "b"), (pkt, "t"), kind="control", label="checksum",
        label_pos=(248, 92), label_anchor="start"))
    d.add(Arrow((fsm, "t"), (gate, "b"), kind="control", label="send, drop",
        label_pos=(433, 195), label_anchor="start"))
    d.add(Arrow((fsm, "r"), (arp, "l"), kind="control", heads="both", label="unicast",
        label_pos=(515, 233)))
    d.add(Arrow((75, buf.y + buf.h), (fsm, "l"), via=[(75, fsm.cy)], kind="control",
        label="destination IP", label_pos=(200, fsm.cy - 9)))
    return d
