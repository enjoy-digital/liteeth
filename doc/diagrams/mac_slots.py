#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Wishbone MAC: SRAM writer into RX slots, SRAM reader from TX slots, registers, CPU.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 260)
    core = d.add(Box(10, 30, 95, 200, label="MAC\ncore"))
    wr   = d.add(Box(145, 30, 130, 50, label="SRAM writer", fill=COLOR_ACCENT))
    regs = d.add(Box(145, 105, 130, 50, label="Registers\nand events", fill=COLOR_ACCENT_LIGHT))
    rd   = d.add(Box(145, 180, 130, 50, label="SRAM reader", fill=COLOR_ACCENT))
    rx   = cells(d, 320, 38, [(120, "RX slot 0", COLOR_ACCENT_LIGHT), (120, "RX slot 1", COLOR_ACCENT_LIGHT)])
    tx   = cells(d, 320, 188, [(120, "TX slot 0", COLOR_ACCENT_LIGHT), (120, "TX slot 1", COLOR_ACCENT_LIGHT)])
    cpu  = d.add(Box(630, 30, 120, 200, label="CPU", fill=COLOR_HARDWARE))
    d.add(Arrow((core.x + core.w, wr.cy), (wr, "l"), kind="data", label="RX", label_pos=(125, wr.cy - 10)))
    d.add(Arrow((rd, "l"), (core.x + core.w, rd.cy), kind="data", label="TX", label_pos=(125, rd.cy - 10)))
    d.add(Arrow((wr, "r"), (rx[0], "l"), kind="data"))
    d.add(Arrow((tx[0], "l"), (rd, "r"), kind="data"))
    d.add(Arrow((rx[1], "r"), (cpu.x, rx[1].cy), kind="data", label="read", label_pos=(605, rx[1].cy - 10)))
    d.add(Arrow((cpu.x, tx[1].cy), (tx[1], "r"), kind="data", label="write", label_pos=(605, tx[1].cy - 10)))
    d.add(Arrow((wr, "b"), (regs, "t"), kind="control"))
    d.add(Arrow((regs, "b"), (rd, "t"), kind="control"))
    d.add(Arrow((regs, "r"), (cpu.x, regs.cy), kind="control", heads="both", label="slot, length, IRQ",
        label_pos=(450, regs.cy - 9)))
    return d
