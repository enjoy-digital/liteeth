#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Hybrid MAC: RX address filter to the hardware stack and the CPU slots, TX arbiter.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 270)
    core = d.add(Box(10, 30, 95, 220, label="MAC\ncore"))
    filt = d.add(Box(150, 30, 150, 50, label="Address filter", fill=COLOR_ACCENT))
    arb  = d.add(Box(150, 200, 150, 50, label="TX arbiter", fill=COLOR_ACCENT))
    hw   = d.add(Box(480, 30, 250, 50, label="Hardware stack", fill=COLOR_ACCENT_LIGHT))
    sram = d.add(Box(480, 120, 250, 50, label="SRAM slots for the CPU", fill=COLOR_ACCENT_LIGHT))
    d.add(Arrow((core.x + core.w, filt.cy), (filt, "l"), kind="data", label="RX",
        label_pos=(127, filt.cy - 10)))
    d.add(Arrow((filt, "r"), (hw, "l"), kind="data", label="local, group",
        label_pos=(390, filt.cy - 10)))
    d.add(Arrow((225, filt.y + filt.h), (sram, "l"), via=[(225, sram.cy)], kind="data",
        label="not local", label_pos=(390, sram.cy - 10)))
    d.add(Arrow((520, sram.y + sram.h), (arb, "r", 0.3), via=[(520, 215)], kind="data"))
    d.add(Arrow((hw, "r"), (arb, "r", 0.7), via=[(752, hw.cy), (752, 235)], kind="data"))
    d.add(Arrow((arb, "l"), (core.x + core.w, arb.cy), kind="data", label="TX",
        label_pos=(127, arb.cy - 10)))
    return d
