#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# MAC RX datapath: PHY, preamble, CRC and padding checks, drop FIFO, CDC and converter; counters.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 240)
    labels = ["PHY", "Preamble\ncheck", "CRC32\ncheck", "Padding\ncheck", "Drop\nFIFO",
        "CDC and\nconverter"]
    fills  = [COLOR_FILL, COLOR_ACCENT, COLOR_ACCENT, COLOR_ACCENT, COLOR_ACCENT_LIGHT,
        COLOR_ACCENT_LIGHT]
    boxes = chain(d, 14, 45, 96, 60, 24, labels, fills)
    d.add(Arrow((boxes[-1], "r"), (CANVAS_WIDTH, 75), kind="data"))
    regs = d.add(Box(135, 185, 460, 46, label="Error and drop counters (sys)"))
    for b, label in ((boxes[1], "preamble"), (boxes[2], "CRC"), (boxes[4], "drop")):
        d.add(Arrow((b.cx, b.y + b.h), (b.cx, regs.y), kind="control", label=label,
            label_pos=(b.cx + 8, 148), label_anchor="start"))
    d.add(Text(boxes[4].cx, 26, "optional", size=13))
    return d
