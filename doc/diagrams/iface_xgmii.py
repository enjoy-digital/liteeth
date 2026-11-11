#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# A frame on a 64-bit XGMII: eight byte lanes over four clock cycles, /S/ in lane 4, data, /T/
# after the last byte, idles.

from primer_helpers import *

WORDS = [
    "I I I I S D D D",
    "D D D D D D D D",
    "D D D D D D D D",
    "D D D T I I I I",
]
LABEL = {"I": "/I/", "S": "/S/", "T": "/T/", "D": "data"}
FILL  = {"I": COLOR_SHADE, "S": COLOR_ACCENT_LIGHT, "T": COLOR_ACCENT_LIGHT, "D": COLOR_ACCENT}


def build():
    d = Diagram(CANVAS_WIDTH, 300)
    x0, y0, cw, rh = 170, 40, 120, 28
    for c in range(len(WORDS)):
        d.add(Text(x0 + c*cw + cw/2, y0 - 18, f"cycle {c + 1}", size=13, color="#444444"))
    for lane in range(8):
        row_name(d, x0, y0 + lane*rh + rh/2, f"Lane {lane}")
        for c, word in enumerate(WORDS):
            k = word.split()[lane]
            d.add(Box(x0 + c*cw, y0 + lane*rh, cw, rh, label=LABEL[k], radius=0, fill=FILL[k], size=13))
    d.add(Text(x0 + 2*cw, y0 + 8*rh + 22, "64 data bits and 8 control bits per clock cycle", size=13,
        color="#444444"))
    return d
