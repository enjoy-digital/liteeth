#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Two frames back to back on the wire: preamble and SFD, frame, inter-frame gap, next frame.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 120)
    F = COLOR_SHADE
    cells = [
        (70,  "Preamble\n+ SFD", F),
        (190, "Frame 1", COLOR_ACCENT),
        (90,  "Gap", COLOR_FILL),
        (70,  "Preamble\n+ SFD", F),
        (190, "Frame 2", COLOR_ACCENT),
        (90,  "Gap", COLOR_FILL),
    ]
    x0 = 25
    bitfield(d, x0, 20, cells, h=50, size=14)
    scale(d, x0, 85, [(70, "8 bytes"), (190, "64 to 1518 bytes"), (90, "12 bytes min."), (70, "8 bytes"),
        (190, "64 to 1518 bytes"), (90, "12 bytes min.")])
    d.add(Text(380, 108, "time", size=12, color="#444444"))
    d.add(Arrow((410, 108), (500, 108), kind="control"))
    return d
