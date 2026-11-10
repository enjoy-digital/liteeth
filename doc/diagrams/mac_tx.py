#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# MAC TX datapath: CDC and converter, padding, CRC, preamble, packet FIFO, gap, PHY.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 150)
    labels = ["CDC and\nconverter", "Padding\nto 60 B", "CRC32\ninsert", "Preamble\ninsert",
        "Packet\nFIFO", "Inter-frame\ngap", "PHY"]
    fills  = [COLOR_ACCENT_LIGHT, COLOR_ACCENT, COLOR_ACCENT, COLOR_ACCENT, COLOR_ACCENT_LIGHT,
        COLOR_ACCENT, COLOR_FILL]
    boxes = chain(d, 14, 60, 88, 60, 19, labels, fills)
    d.add(Arrow((0, 90), (boxes[0], "l"), kind="data"))
    d.add(Text(boxes[0].cx, 140, "sys to eth_tx", size=13))
    d.add(Text(boxes[4].cx, 140, "optional", size=13))
    d.add(Text(boxes[5].cx, 140, "or in the PHY", size=13))
    d.add(Text((boxes[1].x + boxes[3].x + boxes[3].w)/2, 35, "frame formatting", size=13))
    return d
