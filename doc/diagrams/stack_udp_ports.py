#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# UDP user port from get_port(): CDC, width converter, optional packet FIFOs, UDP crossbar.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 290)
    w, h = 120, 50
    user = d.add(Box(10, 50, 100, 220, label="User\nlogic"))
    xbar = d.add(Box(640, 50, 110, 220, label="UDP\ncrossbar", fill=COLOR_ACCENT))
    tx = [d.add(Box(x, 70, w, h, label=l, fill=f)) for x, l, f in (
        (145, "CDC", COLOR_ACCENT_LIGHT), (305, "Converter", COLOR_ACCENT_LIGHT),
        (465, "Packet FIFO", COLOR_FILL))]
    rx = [d.add(Box(x, 200, w, h, label=l, fill=f)) for x, l, f in (
        (145, "CDC", COLOR_ACCENT_LIGHT), (305, "Converter", COLOR_ACCENT_LIGHT),
        (465, "Drop FIFO", COLOR_FILL))]
    d.add(Arrow((user.x + user.w, tx[0].cy), (tx[0], "l"), kind="data", label="TX",
        label_pos=(127, tx[0].cy - 10)))
    for a, b in zip(tx, tx[1:]):
        d.add(Arrow((a, "r"), (b, "l"), kind="data"))
    d.add(Arrow((tx[2], "r"), (xbar.x, tx[2].cy), kind="data"))
    d.add(Arrow((xbar.x, rx[2].cy), (rx[2], "r"), kind="data"))
    for a, b in zip(rx[1:], rx):
        d.add(Arrow((a, "l"), (b, "r"), kind="data"))
    d.add(Arrow((rx[0], "l"), (user.x + user.w, rx[0].cy), kind="data", label="RX",
        label_pos=(127, rx[0].cy - 10)))
    boundary(d, tx[0].cx, 30, 280, [(tx[0].y, tx[0].y + h), (rx[0].y, rx[0].y + h)])
    d.add(Text(tx[0].cx - 10, 20, "port cd", size=13, anchor="end"))
    d.add(Text(tx[0].cx + 10, 20, "sys of the core", size=13, anchor="start"))
    d.add(Text(tx[2].cx, 160, "optional", size=13))
    return d
