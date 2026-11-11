#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Shared helpers of the Part I figures (primer_*, iface_*, net_*; no build(): not a figure).
#
# Bit fields: rows of adjoining square cells. Exchanges: one vertical lane per side (a titled box
# on top and a dashed line below), horizontal arrows between the lanes for the messages.

from svgdiag import *


def bitfield(d, x, y, cells, h=40, size=13):
    """Row of adjoining square cells: cells is a list of (width, label, fill); returns the boxes."""
    out = []
    for w, label, fill in cells:
        out.append(d.add(Box(x, y, w, h, label=label, radius=0, fill=fill, size=size)))
        x += w
    return out


def scale(d, x, y, cells, size=12):
    """Labels centred under a row of cells: cells is a list of (width, text)."""
    for w, text in cells:
        if text:
            d.add(Text(x + w/2, y, text, size=size, color="#444444"))
        x += w


class Exchange:
    """Two-lane exchange diagram: left and right lanes at x_left/x_right."""
    def __init__(self, d, left, right, x_left=170, x_right=590, top=10, bottom=None, fill_left=COLOR_ACCENT,
        fill_right=COLOR_HARDWARE):
        self.d, self.xl, self.xr = d, x_left, x_right
        bottom = bottom or d.height - 10
        for x, name, fill in ((x_left, left, fill_left), (x_right, right, fill_right)):
            d.add(Box(x - 70, top, 140, 36, label=name, fill=fill))
            d.add(Bus([(x, top + 36), (x, bottom)], dash="4,3"))
        self.y = top + 36 + 30

    def msg(self, label, to="right", kind="control", step=40, note=None):
        """Arrow between the lanes at the current row, label above; returns the row y."""
        y = self.y
        a, b = ((self.xl, y), (self.xr, y)) if to == "right" else ((self.xr, y), (self.xl, y))
        self.d.add(Arrow(a, b, kind=kind, label=label, label_side="above", label_size=14))
        if note:
            if to == "right":
                self.d.add(Text(self.xr + 12, y, note, size=13, anchor="start", color="#444444"))
            else:
                self.d.add(Text(self.xl - 12, y, note, size=13, anchor="end", color="#444444"))
        self.y += step
        return y

    def note(self, text, side="left", step=26):
        """Free note on one side of the current row (no arrow)."""
        y = self.y - 10
        if side == "left":
            self.d.add(Text(self.xl - 12, y, text, size=13, anchor="end", color="#444444"))
        else:
            self.d.add(Text(self.xr + 12, y, text, size=13, anchor="start", color="#444444"))
        self.y += step


# Timing rows: one horizontal row per signal (a name on the left, adjoining cells on a common time
# axis), clock rows drawn as square waves, spans (double-headed arrows) for the delays.

def row_name(d, x, y, name, size=13):
    """Signal name, right-aligned, left of a row whose cells start at x and are centred on y."""
    d.add(Text(x - 10, y, name, size=size, anchor="end"))


def clock(d, x, y, period, n, h=18, phase=0.0):
    """Square wave of n periods starting at x, low at y + h/2, high at y - h/2.

    phase shifts the first rising edge by a fraction of a period (0.25 = 90 degrees)."""
    hi, lo = y - h/2, y + h/2
    pts = [(x, lo)]
    t = x + phase*period
    if phase:
        pts.append((t, lo))
    for i in range(n):
        r = t + i*period
        f = r + period/2
        pts += [(r, lo), (r, hi), (f, hi), (f, lo)]
    pts.append((t + n*period, lo))
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 0.5 or abs(p[1] - clean[-1][1]) > 0.5:
            clean.append(p)
    d.add(Bus(clean, kind="control"))


def tick(d, x, y0, y1):
    """Dotted vertical marker (a time reference) from y0 to y1."""
    d.add(Bus([(x, y0), (x, y1)], dash="2,3", color="#9a9a9a"))
