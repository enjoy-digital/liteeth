#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Clock domains: user port clock, core sys, PHY eth_tx/eth_rx, with the CDCs between them.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 200)
    y, h = 80, 64
    user = d.add(Box(10,  y, 100, h, label="User logic"))
    pcdc = d.add(Box(140, y, 80,  h, label="Port CDC", fill=COLOR_ACCENT_LIGHT))
    core = d.add(Box(250, y, 150, h, label="ARP, IP, UDP\ncrossbars", fill=COLOR_ACCENT))
    mcdc = d.add(Box(430, y, 80,  h, label="MAC CDC", fill=COLOR_ACCENT_LIGHT))
    fmt  = d.add(Box(540, y, 120, h, label="MAC framing", fill=COLOR_ACCENT))
    phy  = d.add(Box(690, y, 60,  h, label="PHY", fill=COLOR_ACCENT_LIGHT))
    for a, b in ((user, pcdc), (pcdc, core), (core, mcdc), (mcdc, fmt), (fmt, phy)):
        d.add(Arrow((a, "r"), (b, "l"), kind="data", heads="both"))
    boundary(d, pcdc.cx, 45, 185, [(pcdc.y, pcdc.y + pcdc.h)])
    boundary(d, mcdc.cx, 45, 185, [(mcdc.y, mcdc.y + mcdc.h)])
    d.add(Text((10 + pcdc.cx)/2,      30, "port clock domain", size=13))
    d.add(Text((pcdc.cx + mcdc.cx)/2, 30, "sys of the core", size=13))
    d.add(Text((mcdc.cx + 750)/2,     30, "eth_tx, eth_rx", size=13))
    return d
