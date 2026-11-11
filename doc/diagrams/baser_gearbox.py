#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 7-Series 64b/66b gearbox cadence over the 33-cycle sequence: Kintex-7 GTX carries one block per
# cycle with a pause at 32; Artix-7 GTP carries one 32-bit half block per cycle with a pause at 31.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 230)
    cycles = ["0", "1", "2", "3", None, "29", "30", "31", "32"]
    k7     = ["block", "block", "block", "block", None, "block", "block", "block", "pause"]
    a7     = ["upper", "lower", "upper", "lower", None, "lower", "upper", "pause", "lower"]
    x0, w  = 130, 68
    for i, c in enumerate(cycles):
        x = x0 + i*w
        if c is not None:
            d.add(Text(x + w/2, 35, c, size=13))
    for row, (name, cells, y) in enumerate([("K7 GTX", k7, 55), ("A7 GTP", a7, 135)]):
        d.add(Text(20, y + 25, name, anchor="start"))
        for i, c in enumerate(cells):
            x = x0 + i*w
            if c is None:
                continue
            fill = COLOR_SHADE if c == "pause" else COLOR_ACCENT
            d.add(Box(x, y, w, 50, label=c, radius=0, size=13, fill=fill))
    d.add(Text(x0 + 4.5*w, 80, "x 25", size=13))
    d.add(Text(x0 + 4.5*w, 160, "x 25", size=13))
    d.add(Text(130, 210, "33 cycles of the 64-bit or 32-bit user clock, sequence 0 to 32", size=13,
        anchor="start"))
    return d
