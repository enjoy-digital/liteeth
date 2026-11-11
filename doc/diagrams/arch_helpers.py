#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Shared helpers of the core figures (arch_*, mac_*, stack_*, fe_*, ptp_*): byte cells, state
# boxes, chains of blocks and dotted clock boundaries. Not a figure (no build()).

from svgdiag import *

# Cells --------------------------------------------------------------------------------------------

def cells(d, x, y, items, h=34, size=FONT_SIZE):
    """Adjoining square cells: items is a list of (width, label, fill); returns the boxes."""
    boxes = []
    for w, label, fill in items:
        boxes.append(d.add(Box(x, y, w, h, label=label, radius=0, fill=fill, size=size)))
        x += w
    return boxes

# States -------------------------------------------------------------------------------------------

def state(d, x, y, label, w=150, h=46, fill=COLOR_FILL, size=FONT_SIZE):
    """One state of a state machine: a box of a fixed size, so that rows and columns line up."""
    return d.add(Box(x, y, w, h, label=label, fill=fill, size=size))

# Chains -------------------------------------------------------------------------------------------

def chain(d, x, y, w, h, gap, labels, fills=None, kind="data", size=FONT_SIZE, reverse=False):
    """A row of boxes of the same size linked by arrows; returns the boxes (left to right)."""
    fills = fills or [COLOR_FILL]*len(labels)
    boxes = [d.add(Box(x + i*(w + gap), y, w, h, label=l, fill=f, size=size))
        for i, (l, f) in enumerate(zip(labels, fills))]
    for a, b in zip(boxes, boxes[1:]):
        if reverse:
            d.add(Arrow((b, "l"), (a, "r"), kind=kind))
        else:
            d.add(Arrow((a, "r"), (b, "l"), kind=kind))
    return boxes

# Clock boundaries ---------------------------------------------------------------------------------

def boundary(d, x, top, bottom, gaps=()):
    """Dotted clock boundary from top to bottom, interrupted where it crosses a box."""
    y = top
    for g0, g1 in gaps:
        d.add(Bus([(x, y), (x, g0)], dash=DASH_FEEDBACK))
        y = g1
    d.add(Bus([(x, y), (x, bottom)], dash=DASH_FEEDBACK))
