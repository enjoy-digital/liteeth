#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# PTP slave: UDP ports 319/320, RX parsers, timestamp latch, TX, control FSM, servo, TSU.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 320)
    w, h = 130, 46
    ports = d.add(Box(10, 30, 85, 270, label="UDP\nports\n319, 320"))
    rxev  = d.add(Box(135, 30,  w, h, label="Event RX", fill=COLOR_ACCENT_LIGHT))
    rxge  = d.add(Box(135, 100, w, h, label="General RX", fill=COLOR_ACCENT_LIGHT))
    latch = d.add(Box(135, 170, w, h, label="RX first beat", fill=COLOR_ACCENT_LIGHT))
    tx    = d.add(Box(135, 254, w, h, label="Delay request", fill=COLOR_ACCENT_LIGHT))
    ctl   = d.add(Box(330, 30, 130, 186, label="Control\nstate\nmachine", fill=COLOR_ACCENT))
    servo = d.add(Box(545, 30, 130, 70, label="Servo", fill=COLOR_ACCENT))
    tsu   = d.add(Box(545, 170, 130, 130, label="TSU\nseconds,\nnanoseconds", fill=COLOR_ACCENT))
    for b in (rxev, rxge, latch):
        d.add(Arrow((ports.x + ports.w, b.cy), (b, "l"), kind="data"))
    d.add(Arrow((tx, "l"), (ports.x + ports.w, tx.cy), kind="data"))
    d.add(Arrow((rxev, "r"), (ctl.x, rxev.cy), kind="control"))
    d.add(Arrow((rxge, "r"), (ctl.x, rxge.cy), kind="control"))
    d.add(Arrow((latch, "r"), (ctl.x, latch.cy), kind="control"))
    d.add(Arrow((395, ctl.y + ctl.h), (tx, "t"), via=[(395, 236), (200, 236)], kind="control",
        label="start", label_pos=(300, 228)))
    d.add(Arrow((tx, "r"), (tsu.x, tx.cy), kind="control", label="TX latch",
        label_pos=(470, tx.cy - 9)))
    d.add(Arrow((ctl.x + ctl.w, 65), (servo.x, 65), kind="control", label="t1 to t4",
        label_pos=(502, 56)))
    d.add(Arrow((servo, "b"), (tsu, "t"), kind="control", label="phase, addend, step",
        label_pos=(618, 140), label_anchor="start"))
    d.add(Arrow((tsu.x, 190), (ctl.x + ctl.w, 190), kind="control", label="timestamps",
        label_pos=(502, 181)))
    d.add(Arrow((ctl.x + ctl.w, 208), (tsu.x, 208), kind="control", label="RX latch",
        label_pos=(502, 226)))
    d.add(Arrow((tsu, "r"), (CANVAS_WIDTH, tsu.cy), kind="data", label="time",
        label_pos=(718, tsu.cy - 10)))
    return d
