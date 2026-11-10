#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# UltraScale LVDS 1000BASE-X receiver: a data and a monitor ISERDESE3 half a bit apart form an
# Alexander phase detector that steps the fine phase of the RX MMCM.

from svgdiag import *


def frac(box, y):
    return (y - box.y)/box.h


def build():
    d = Diagram(CANVAS_WIDTH, 300)
    pair = d.add(Box(15, 40, 95, 200, label="RX pair", fill=COLOR_HARDWARE))
    data = d.add(Box(160, 40, 140, 55, label="Data\nISERDESE3", fill=COLOR_ACCENT))
    idly = d.add(Box(160, 130, 140, 55, label="IDELAYE3\nhalf bit"))
    mon  = d.add(Box(345, 130, 140, 55, label="Monitor\nISERDESE3", fill=COLOR_ACCENT))
    pd   = d.add(Box(530, 130, 140, 55, label="Phase\ndetector", fill=COLOR_ACCENT))
    mmcm = d.add(Box(530, 225, 140, 55, label="MMCM\nfine phase"))
    gb   = d.add(Box(345, 40, 140, 55, label="Gearbox\ncomma align", fill=COLOR_ACCENT_LIGHT))
    pcs  = d.add(Box(530, 40, 140, 55, label="PCS", fill=COLOR_ACCENT_LIGHT))
    d.add(Arrow((pair, "r", frac(pair, 67.5)), (data, "l"), kind="accent"))
    d.add(Arrow((pair, "r", frac(pair, 157.5)), (idly, "l"), kind="data"))
    d.add(Arrow((idly, "r"), (mon, "l"), kind="data"))
    d.add(Arrow((data, "r"), (gb, "l"), kind="accent"))
    d.add(Arrow((gb, "r"), (pcs, "l"), kind="accent"))
    d.add(Arrow((mon, "r"), (pd, "l"), kind="data"))
    d.add(Arrow((data, "b", 0.85), (pd, "t", 0.15), kind="data", via=[(279, 112), (551, 112)]))
    d.add(Arrow((pd, "b"), (mmcm, "t"), kind="control", label="step", label_pos=(630, 205)))
    d.add(Arrow((mmcm, "l"), (mon, "b"), kind="feedback", via=[(415, 252.5)],
        label="clocks to both", label_pos=(472, 242)))
    return d
