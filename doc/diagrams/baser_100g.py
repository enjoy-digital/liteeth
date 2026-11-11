#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 100G path (in development): UDP/IP core, AXI-Stream frame adapter, generated UltraScale+ CMAC
# with its four GTY lanes, module without FEC.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 180)
    d.add(Container(150, 15, 465, 150, title="USP_CMAC_100G", title_pos="br", title_size=15))
    core = d.add(Box(15, 50, 115, 70, label="UDP/IP core\nor user", fill=COLOR_ACCENT_LIGHT))
    axis = d.add(Box(170, 50, 120, 70, label="AXI adapter\nFIFOs", fill=COLOR_ACCENT))
    cmac = d.add(Box(320, 50, 130, 70, label="CMAC IP\nMAC, PCS", fill=COLOR_HARDWARE))
    gty  = d.add(Box(470, 50, 125, 70, label="4 x GTY\n25.78125 Gbps", fill=COLOR_HARDWARE, size=13))
    mod  = d.add(Box(640, 50, 105, 70, label="Module\nno FEC", fill=COLOR_HARDWARE))
    d.add(Arrow((core, "r", 0.3), (axis, "l", 0.3), kind="data"))
    d.add(Arrow((axis, "l", 0.7), (core, "r", 0.7), kind="data"))
    d.add(Arrow((axis, "r", 0.3), (cmac, "l", 0.3), kind="accent", label="512-bit", label_pos=(305, 40)))
    d.add(Arrow((cmac, "l", 0.7), (axis, "r", 0.7), kind="accent"))
    d.add(Arrow((cmac, "r"), (gty, "l"), kind="data", heads="both"))
    d.add(Arrow((gty, "r"), (mod, "l"), kind="data", heads="both"))
    return d
