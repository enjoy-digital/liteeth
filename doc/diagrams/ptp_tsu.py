#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# TSU: addend, multiply, fraction accumulator, nanosecond counter, servo corrections, latches.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 240)
    y, h = 30, 64
    add  = d.add(Box(10,  y, 110, h, label="Addend\n32 + 20 bits", fill=COLOR_ACCENT_LIGHT))
    mul  = d.add(Box(150, y, 100, h, label="Multiply\nby 10^9", fill=COLOR_ACCENT_LIGHT))
    acc  = d.add(Box(280, y, 120, h, label="Fraction\naccumulator", fill=COLOR_ACCENT_LIGHT))
    nsa  = d.add(Box(430, y, 120, h, label="Add and\nwrap at 1 s", fill=COLOR_ACCENT))
    time = d.add(Box(580, y, 170, h, label="seconds (48)\nnanoseconds (32)", fill=COLOR_ACCENT))
    srv  = d.add(Box(300, 155, 250, 50, label="Servo corrections"))
    lat  = d.add(Box(650, 155, 100, 50, label="Latches", fill=COLOR_ACCENT_LIGHT))
    for a, b in ((add, mul), (mul, acc), (acc, nsa), (nsa, time)):
        d.add(Arrow((a, "r"), (b, "l"), kind="data"))
    d.add(Arrow((srv, "r"), (615, time.y + h), via=[(615, srv.cy)], kind="control",
        label="offset, step", label_pos=(568, srv.cy - 9), label_anchor="start"))
    d.add(Arrow((srv, "l"), (65, add.y + h), via=[(65, srv.cy)], kind="control",
        label="addend", label_pos=(180, srv.cy - 9)))
    d.add(Arrow((700, time.y + h), (lat, "t"), kind="control"))
    return d
