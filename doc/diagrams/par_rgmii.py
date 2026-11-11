#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 7-Series RGMII PHY: DDR outputs for TXD/TX_CTL, a PLL shifting the transmit clock by 90 degrees,
# fixed input delays and DDR inputs on the receive side.

from svgdiag import *


def frac(box, y):
    return (y - box.y)/box.h


def build():
    d = Diagram(CANVAS_WIDTH, 320)
    d.add(Container(135, 15, 455, 290, title="RGMII PHY", title_pos="tr"))
    mac   = d.add(Box(15, 45, 95, 245, label="MAC", fill=COLOR_ACCENT_LIGHT))
    oddr  = d.add(Box(400, 55, 150, 50, label="ODDR\nTXD, TX_CTL", fill=COLOR_ACCENT))
    pll   = d.add(Box(165, 125, 150, 50, label="PLL\n90° shift"))
    oclk  = d.add(Box(400, 125, 150, 50, label="ODDR\nTXC", fill=COLOR_ACCENT))
    iddr  = d.add(Box(165, 230, 150, 50, label="IDDR\nRXD, RX_CTL", fill=COLOR_ACCENT))
    idly  = d.add(Box(400, 230, 150, 50, label="IDELAY\n2 ns", fill=COLOR_ACCENT))
    ext   = d.add(Box(640, 45, 105, 245, label="External\nPHY", fill=COLOR_HARDWARE))
    yt, yc, yr, yk = 80, 150, 255, 205
    d.add(Arrow((mac, "r", frac(mac, yt)), (oddr, "l"), kind="accent"))
    d.add(Arrow((oddr, "r"), (ext, "l", frac(ext, yt)), kind="accent", label="TXD", label_pos=(615, 70)))
    d.add(Arrow((pll, "r"), (oclk, "l"), kind="control", label="shifted", label_pos=(357, 140)))
    d.add(Arrow((oclk, "r"), (ext, "l", frac(ext, yc)), kind="control", label="TXC", label_pos=(615, 140)))
    d.add(Arrow((ext, "l", frac(ext, yr)), (idly, "r"), kind="data", label="RXD", label_pos=(615, 245)))
    d.add(Arrow((idly, "l"), (iddr, "r"), kind="data"))
    d.add(Arrow((iddr, "l"), (mac, "r", frac(mac, yr)), kind="data"))
    d.add(Arrow((ext, "l", frac(ext, yk)), (pll, "b", 0.5), kind="feedback", via=[(240, yk)],
        label="RXC clocks eth_rx", label_pos=(470, 195)))
    return d
