#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# LiteEth at a glance: user logic, frontends, the core (hardware stack and MAC), the PHY, the
# external PHY chip or SFP module, and the link partner.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 175)
    d.add(Container(208, 15, 254, 150, title="Core", title_pos="bl"))
    user  = d.add(Box(10,  50, 75, 70, label="User\nlogic", fill=COLOR_FILL))
    fe    = d.add(Box(105, 50, 85, 70, label="Frontends", stack=2, size=14, fill=COLOR_ACCENT_LIGHT))
    stack = d.add(Box(225, 50, 110, 70, label="ARP, IP,\nICMP, UDP", fill=COLOR_ACCENT))
    mac   = d.add(Box(360, 50, 80, 70, label="MAC", fill=COLOR_ACCENT))
    phy   = d.add(Box(485, 50, 70, 70, label="PHY", fill=COLOR_ACCENT_LIGHT))
    ext   = d.add(Box(580, 50, 90, 70, label="External\nPHY or SFP", size=14, fill=COLOR_HARDWARE))
    lp    = d.add(Box(690, 50, 60, 70, label="Link\npartner", size=13, fill=COLOR_HARDWARE))
    d.add(Arrow((user, "r"), (fe, "l"), kind="data", heads="both"))
    d.add(Arrow((fe.x + fe.w + 2*STACK_DX, fe.cy), (stack, "l"), kind="data", heads="both"))
    d.add(Arrow((stack, "r"), (mac, "l"), kind="data", heads="both"))
    d.add(Arrow((mac, "r"), (phy, "l"), kind="data", heads="both"))
    d.add(Arrow((phy, "r"), (ext, "l"), kind="data", heads="both"))
    d.add(Arrow((ext, "r"), (lp, "l"), kind="data", heads="both"))
    return d
