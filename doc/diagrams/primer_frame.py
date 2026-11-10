#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# An Ethernet frame on the wire: preamble, SFD, addresses, EtherType, payload, FCS (sizes in bytes).

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 150)
    F = COLOR_SHADE
    cells = [
        (85,  "Preamble", F),
        (45,  "SFD", F),
        (100, "Destination", COLOR_ACCENT_LIGHT),
        (100, "Source", COLOR_ACCENT_LIGHT),
        (85,  "EtherType", COLOR_ACCENT_LIGHT),
        (200, "Payload", COLOR_ACCENT),
        (65,  "FCS", COLOR_ACCENT_LIGHT),
    ]
    x0 = 40
    bitfield(d, x0, 30, cells, h=44, size=14)
    scale(d, x0, 90, [(85, "7"), (45, "1"), (100, "6"), (100, "6"), (85, "2"), (200, "46 to 1500"), (65, "4")])
    # Frame span: from the destination to the FCS.
    fx0, fx1 = x0 + 130, x0 + 680
    d.add(Arrow((fx0, 112), (fx1, 112), kind="control", heads="both", label="frame: 64 to 1518 bytes",
        label_pos=((fx0 + fx1)/2, 132), label_size=13))
    d.add(Text(x0 + 65, 112, "on the wire only", size=12, color="#444444"))
    return d
