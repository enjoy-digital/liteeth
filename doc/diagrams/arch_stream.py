#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# A 20-byte packet on a 64-bit stream: three beats, byte lanes, be and last.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 210)
    x0, cw, y0, rh = 110, 56, 50, 46
    for lane in range(8):
        d.add(Text(x0 + lane*cw + cw/2, 30, f"lane {lane}", size=13))
    d.add(Text(625, 30, "be", size=13))
    d.add(Text(705, 30, "last", size=13))
    for beat in range(3):
        y = y0 + beat*rh
        items = []
        for lane in range(8):
            n = beat*8 + lane
            if n < 20:
                fill = COLOR_ACCENT if beat == 2 else COLOR_ACCENT_LIGHT
                items.append((cw, f"byte {n}", fill))
            else:
                items.append((cw, "", COLOR_SHADE))
        cells(d, x0, y, items, h=34, size=13)
        d.add(Text(50, y + 17, f"beat {beat}", size=15))
        d.add(Text(625, y + 17, "0x0f" if beat == 2 else "0xff", size=15))
        d.add(Text(705, y + 17, "1" if beat == 2 else "0", size=15))
    return d
