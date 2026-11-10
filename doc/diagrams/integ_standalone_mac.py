#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Standalone MAC core (liteeth_gen, core: wishbone or axi-lite): the port groups of the generated
# liteeth_core module.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 265)
    d.add(Container(215, 20, 330, 230, title="liteeth_core", title_pos="bl"))
    clk  = d.add(Box(20, 45, 150, 40, label="sys_clock, sys_reset"))
    bus  = d.add(Box(20, 110, 150, 50, label="wishbone, or\nbus (AXI-Lite)", fill=COLOR_ACCENT_LIGHT))
    irq  = d.add(Box(20, 185, 150, 40, label="interrupt"))
    regs = d.add(Box(240, 45, 110, 130, label="Registers,\nSRAM slots", fill=COLOR_ACCENT))
    mac  = d.add(Box(380, 45, 140, 50, label="MAC", fill=COLOR_ACCENT))
    phy  = d.add(Box(380, 145, 140, 50, label="PHY", fill=COLOR_ACCENT_LIGHT))
    ext  = d.add(Box(590, 145, 150, 50, label="PHY pins\n(mii)", fill=COLOR_HARDWARE))
    d.add(Arrow((clk, "r"), (240, 65), kind="control"))
    d.add(Arrow((bus, "r"), (240, 135), kind="accent", heads="both"))
    d.add(Arrow((295, 175), (irq, "r"), via=[(295, 205)], kind="control"))
    d.add(Arrow((350, 70), (mac, "l"), kind="data", heads="both"))
    d.add(Arrow((mac, "b"), (phy, "t"), kind="data", heads="both"))
    d.add(Arrow((phy, "r"), (ext, "l"), kind="data", heads="both"))
    return d
