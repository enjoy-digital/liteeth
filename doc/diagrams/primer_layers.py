#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# The IEEE 802.3 sublayers and who implements them: with an external PHY chip (MII to RGMII
# boards) and with an FPGA transceiver and an SFP module (BASE-X and BASE-R boards).

from svgdiag import *

ROWS = ["MAC", "RS", "xMII", "PCS", "PMA", "PMD", "MDI"]
Y0, RH, GAP = 50, 34, 6


def _y(row):
    return Y0 + row*(RH + GAP)


def _span(d, x, w, first, last, label, fill):
    y0 = _y(first)
    y1 = _y(last) + RH
    return d.add(Box(x, y0, w, y1 - y0, label=label, fill=fill))


def build():
    d = Diagram(CANVAS_WIDTH, Y0 + len(ROWS)*(RH + GAP) + 4)
    for i, name in enumerate(ROWS):
        d.add(Box(20, _y(i), 90, RH, label=name, radius=0, fill=COLOR_FILL, size=14))
    d.add(Text(290, 22, "External PHY chip", size=15, italic=True))
    d.add(Text(600, 22, "Transceiver and SFP", size=15, italic=True))
    xa, xb, w = 150, 460, 280
    _span(d, xa, w, 0, 0, "LiteEth MAC", COLOR_ACCENT)
    _span(d, xa, w, 1, 2, "LiteEth PHY (MII to RGMII)", COLOR_ACCENT_LIGHT)
    _span(d, xa, w, 3, 5, "External PHY chip", COLOR_HARDWARE)
    _span(d, xa, w, 6, 6, "RJ45 and magnetics", COLOR_HARDWARE)
    _span(d, xb, w, 0, 0, "LiteEth MAC", COLOR_ACCENT)
    _span(d, xb, w, 1, 3, "LiteEth PHY (PCS)", COLOR_ACCENT_LIGHT)
    _span(d, xb, w, 4, 4, "FPGA transceiver", COLOR_HARDWARE)
    _span(d, xb, w, 5, 6, "SFP module and cage", COLOR_HARDWARE)
    return d
