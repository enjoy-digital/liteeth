#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# The three uses of the MAC: software MAC (CPU and SRAM slots), hardware stack (no CPU) and hybrid
# mode (both on one PHY).

from svgdiag import *


def _col(d, x, title):
    d.add(Container(x, 10, 240, 330, title=title, title_pos="bl"))


def build():
    d = Diagram(CANVAS_WIDTH, 350)
    # Software MAC.
    _col(d, 10, "Software MAC")
    cpu  = d.add(Box(65,  30, 130, 44, label="CPU (BIOS, Linux)", size=14, fill=COLOR_FILL))
    sram = d.add(Box(65, 100, 130, 44, label="SRAM slots", fill=COLOR_ACCENT_LIGHT))
    mac0 = d.add(Box(65, 170, 130, 44, label="MAC", fill=COLOR_ACCENT))
    phy0 = d.add(Box(65, 240, 130, 44, label="PHY", fill=COLOR_ACCENT_LIGHT))
    for a, b in ((cpu, sram), (sram, mac0), (mac0, phy0)):
        d.add(Arrow((a, "b"), (b, "t"), kind="data", heads="both"))
    # Hardware stack.
    _col(d, 260, "Hardware stack")
    usr  = d.add(Box(315,  30, 130, 44, label="User logic", fill=COLOR_FILL))
    stk  = d.add(Box(315, 100, 130, 44, label="ARP, IP, UDP", fill=COLOR_ACCENT_LIGHT))
    mac1 = d.add(Box(315, 170, 130, 44, label="MAC", fill=COLOR_ACCENT))
    phy1 = d.add(Box(315, 240, 130, 44, label="PHY", fill=COLOR_ACCENT_LIGHT))
    for a, b in ((usr, stk), (stk, mac1), (mac1, phy1)):
        d.add(Arrow((a, "b"), (b, "t"), kind="data", heads="both"))
    # Hybrid.
    _col(d, 510, "Hybrid mode")
    cpu2  = d.add(Box(525,  30, 100, 44, label="CPU", fill=COLOR_FILL))
    usr2  = d.add(Box(635,  30, 100, 44, label="User logic", size=14, fill=COLOR_FILL))
    sram2 = d.add(Box(525, 100, 100, 44, label="SRAM slots", size=14, fill=COLOR_ACCENT_LIGHT))
    stk2  = d.add(Box(635, 100, 100, 44, label="ARP, IP, UDP", size=14, fill=COLOR_ACCENT_LIGHT))
    mac2  = d.add(Box(525, 170, 210, 44, label="MAC and filter", fill=COLOR_ACCENT))
    phy2  = d.add(Box(565, 240, 130, 44, label="PHY", fill=COLOR_ACCENT_LIGHT))
    d.add(Arrow((cpu2, "b"), (sram2, "t"), kind="data", heads="both"))
    d.add(Arrow((usr2, "b"), (stk2, "t"), kind="data", heads="both"))
    d.add(Arrow((sram2, "b"), (sram2.cx, 170), kind="data", heads="both"))
    d.add(Arrow((stk2, "b"), (stk2.cx, 170), kind="data", heads="both"))
    d.add(Arrow((mac2.cx, 214), (phy2, "t"), kind="data", heads="both"))
    return d
