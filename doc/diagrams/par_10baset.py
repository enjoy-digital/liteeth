#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 10BASE-T on FPGA IOs: wiring of bench/arty_10baset.py (two outputs drive the transmit pair, an
# LVDS input samples the receive pair, passives between the FPGA and the RJ45).

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 250)
    d.add(Container(15, 15, 175, 220, title="FPGA", title_pos="tr"))
    tx   = d.add(Box(30, 50, 145, 60, label="2 outputs td_p, td_n\n2 bias pins", fill=COLOR_ACCENT))
    rx   = d.add(Box(30, 155, 145, 60, label="LVDS in rd_p, rd_n\n2 bias pins", fill=COLOR_ACCENT))
    tt   = d.add(Box(225, 50, 130, 60, label="2 x 1 kΩ bias\n100 Ω"))
    rt   = d.add(Box(225, 155, 130, 60, label="2 x 1 kΩ bias\n100 Ω"))
    tc   = d.add(Box(385, 50, 105, 60, label="2 x 1 µF"))
    rc   = d.add(Box(385, 155, 105, 60, label="2 x 1 µF"))
    rj   = d.add(Box(520, 50, 95, 165, label="RJ45\n3, 6 out\n1, 2 in", fill=COLOR_HARDWARE))
    lp   = d.add(Box(645, 50, 100, 165, label="Link\npartner", fill=COLOR_HARDWARE))
    d.add(Arrow((tx, "r"), (tt, "l"), kind="data"))
    d.add(Arrow((tt, "r"), (tc, "l"), kind="data"))
    d.add(Arrow((tc, "r"), (rj, "l", 30/165), kind="data"))
    d.add(Arrow((rj, "l", 135/165), (rc, "r"), kind="data"))
    d.add(Arrow((rc, "l"), (rt, "r"), kind="data"))
    d.add(Arrow((rt, "l"), (rx, "r"), kind="data"))
    d.add(Arrow((rj, "r", 30/165), (lp, "l", 30/165), kind="data"))
    d.add(Arrow((lp, "l", 135/165), (rj, "r", 135/165), kind="data"))
    return d
