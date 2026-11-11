#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# PTP servo pipeline: input latch, deltas, outlier check, phase and delay, frequency step, addend; three outcomes.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 250)
    labels = ["Input\nlatch", "Deltas\nt2-t1, t4-t3\n(P2P: p4-p1,\np3-p2)", "Outlier\ncheck",
        "Phase and\ndelay", "Frequency\nstep", "Addend\nclamp"]
    stages = chain(d, 15, 15, 104, 80, 20, labels, [COLOR_ACCENT]*6)
    outs = [d.add(Box(x, 170, 200, 56, label=l, fill=COLOR_ACCENT_LIGHT)) for x, l in (
        (20, "Phase correction\nand new addend"), (280, "Seconds\ncorrection"),
        (540, "Step to the\nmaster time"))]
    last = stages[-1]
    d.add(Bus([(last.cx, last.y + last.h), (last.cx, 125)], kind="data"))
    d.add(Bus([(outs[0].cx, 125), (last.cx, 125)], kind="data"))
    for box, label in zip(outs, ("good exchange", "near 1 s", "far off")):
        d.add(Arrow((box.cx, 125), (box, "t"), kind="data", label=label,
            label_pos=(box.cx + 8, 150), label_anchor="start"))
    return d
