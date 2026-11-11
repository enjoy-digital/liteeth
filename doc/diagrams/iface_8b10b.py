#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# A frame on a 1000BASE-X link: idles, /S/, preamble and frame data, /T/ /R/, idles again.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 125)
    F = COLOR_SHADE
    cells = [
        (80,  "/I/", F),
        (80,  "/I/", F),
        (50,  "/S/", COLOR_ACCENT_LIGHT),
        (95,  "Preamble,\nSFD", COLOR_ACCENT),
        (175, "Frame", COLOR_ACCENT),
        (50,  "/T/", COLOR_ACCENT_LIGHT),
        (50,  "/R/", COLOR_ACCENT_LIGHT),
        (80,  "/I/", F),
    ]
    x0 = 50
    bitfield(d, x0, 20, cells, h=50, size=14)
    scale(d, x0, 88, [(80, "K28.5 Dx.y"), (80, "K28.5 Dx.y"), (50, "K27.7"), (95, "7 bytes"),
        (175, "data code groups"), (50, "K29.7"), (50, "K23.7"), (80, "K28.5 Dx.y")])
    d.add(Text(380, 112, "time", size=12, color="#444444"))
    d.add(Arrow((410, 112), (500, 112), kind="control"))
    return d
