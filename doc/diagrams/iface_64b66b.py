#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 64b/66b blocks: a 2-bit sync header, then eight data bytes (data block) or a block type byte and
# 56 bits of data and control characters (control block).

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 230)
    x0, sw, bw = 150, 60, 65
    d.add(Text(x0 - 10, 50, "Data block", size=14, anchor="end"))
    bitfield(d, x0, 30, [(sw, "01", COLOR_SHADE)] + [(bw, f"D{i}", COLOR_ACCENT) for i in range(8)], h=40, size=14)
    d.add(Text(x0 - 10, 125, "Control block", size=14, anchor="end"))
    bitfield(d, x0, 105, [(sw, "10", COLOR_SHADE), (bw, "Type", COLOR_ACCENT_LIGHT),
        (7*bw, "56 bits: data bytes and 7-bit control characters", COLOR_ACCENT)], h=40, size=14)
    scale(d, x0, 162, [(sw, "sync header"), (8*bw, "64 bits, scrambled")], size=13)
    d.add(Arrow((x0 + sw, 190), (x0 + sw + 8*bw, 190), kind="control", heads="both"))
    d.add(Arrow((x0, 190), (x0 + sw, 190), kind="control", heads="both"))
    d.add(Text(x0 + (sw + 8*bw)/2, 212, "66 bits on the line", size=13, color="#444444"))
    return d
