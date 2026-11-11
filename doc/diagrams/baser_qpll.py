#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Two BASE-R channels sharing one QPLL: the parent instantiates the PLL, one PHY owns its reset
# (pll_master=True), the other follows (pll_master=False).

from svgdiag import *


def frac(box, y):
    return (y - box.y)/box.h


def build():
    d = Diagram(CANVAS_WIDTH, 250)
    d.add(Container(150, 15, 450, 220, title="Parent design", title_pos="tl"))
    ref  = d.add(Box(15, 85, 105, 60, label="Reference\nclock", fill=COLOR_HARDWARE))
    pll  = d.add(Box(185, 85, 120, 60, label="QPLL", fill=COLOR_ACCENT))
    phy0 = d.add(Box(400, 40, 160, 60, label="PHY 0\npll_master=True", fill=COLOR_ACCENT_LIGHT, size=13))
    phy1 = d.add(Box(400, 145, 160, 60, label="PHY 1\npll_master=False", fill=COLOR_ACCENT_LIGHT, size=13))
    sfp0 = d.add(Box(640, 40, 105, 60, label="SFP 0", fill=COLOR_HARDWARE))
    sfp1 = d.add(Box(640, 145, 105, 60, label="SFP 1", fill=COLOR_HARDWARE))
    d.add(Arrow((ref, "r"), (pll, "l"), kind="data"))
    d.add(Arrow((pll, "r", 0.25), (phy0, "l", 0.6), kind="data", via=[(350, 100), (350, 76)]))
    d.add(Arrow((pll, "r", 0.75), (phy1, "l", 0.4), kind="data", via=[(350, 130), (350, 169)]))
    d.add(Arrow((phy0, "l", 0.25), (pll, "t"), kind="control", via=[(245, 55)],
        label="PLL reset", label_pos=(345, 45)))
    d.add(Arrow((phy0, "r"), (sfp0, "l"), kind="data"))
    d.add(Arrow((phy1, "r"), (sfp1, "l"), kind="data"))
    return d
