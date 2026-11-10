#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Etherbone master: packet check, probe, record receiver, Wishbone master, record sender.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 270)
    w, h = 115, 50
    udp  = d.add(Box(10, 30, 75, 220, label="UDP\nport"))
    chk  = d.add(Box(115, 40,  w, h, label="Packet check", fill=COLOR_ACCENT_LIGHT))
    prb  = d.add(Box(115, 115, w, h, label="Probe reply", fill=COLOR_ACCENT_LIGHT))
    ptx  = d.add(Box(115, 190, w, h, label="Packet header", fill=COLOR_ACCENT_LIGHT))
    rcv  = d.add(Box(285, 40,  100, h, label="Record\nreceiver", fill=COLOR_ACCENT))
    snd  = d.add(Box(285, 190, 100, h, label="Record\nsender", fill=COLOR_ACCENT))
    wbm  = d.add(Box(485, 30, 110, 220, label="Wishbone\nmaster", fill=COLOR_ACCENT))
    bus  = d.add(Box(640, 30, 110, 220, label="SoC bus", fill=COLOR_HARDWARE))
    d.add(Arrow((udp.x + udp.w, chk.cy), (chk, "l"), kind="accent"))
    d.add(Arrow((chk, "r"), (rcv, "l"), kind="accent", label="record", label_pos=(258, chk.cy - 10)))
    d.add(Arrow((chk, "b"), (prb, "t"), kind="data", label="probe", label_pos=(180, 103),
        label_anchor="start"))
    d.add(Arrow((prb, "b"), (ptx, "t"), kind="data"))
    d.add(Arrow((rcv, "r"), (wbm.x, rcv.cy), kind="accent", label="accesses",
        label_pos=(440, rcv.cy - 10)))
    d.add(Arrow((wbm.x + wbm.w, 100), (bus.x, 100), kind="accent", heads="both"))
    d.add(Arrow((wbm.x, snd.cy), (snd, "r"), kind="accent", label="read data",
        label_pos=(440, snd.cy - 10)))
    d.add(Arrow((snd, "l"), (ptx, "r"), kind="accent"))
    d.add(Arrow((ptx, "l"), (udp.x + udp.w, ptx.cy), kind="accent"))
    return d
