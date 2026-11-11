#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# MII, RMII, GMII and RGMII: data, control and clock signals between the FPGA and the PHY chip.

from svgdiag import *

PW, PH = 365, 185


def _panel(d, x, y, title, signals, osc=False):
    d.add(Container(x, y, PW, PH, title=title, title_pos="tl", title_size=16))
    top = y + 40
    fpga = d.add(Box(x + 12, top, 70, 120 if not osc else 90, label="FPGA", fill=COLOR_ACCENT))
    phy  = d.add(Box(x + PW - 82, top, 70, 120 if not osc else 90, label="PHY\nchip", fill=COLOR_HARDWARE))
    sy = top + 22
    for label, direction, kind in signals:
        a, b = ((fpga.x + fpga.w, sy), (phy.x, sy)) if direction == "tx" else ((phy.x, sy), (fpga.x + fpga.w, sy))
        d.add(Arrow(a, b, kind=kind, label=label, label_side="above", label_size=12))
        sy += 27 if not osc else 30
    if osc:
        o = d.add(Box(x + PW/2 - 30, y + PH - 42, 60, 30, label="50 MHz", size=12, fill=COLOR_HARDWARE))
        d.add(Arrow((o, "l"), (fpga, "b"), kind="control"))
        d.add(Arrow((o, "r"), (phy, "b"), kind="control"))


def build():
    d = Diagram(CANVAS_WIDTH, 2*PH + 25)
    _panel(d, 10, 10, "MII", [
        ("TXD[3:0], TX_EN",        "tx", "data"),
        ("RXD[3:0], RX_DV, RX_ER", "rx", "data"),
        ("TX_CLK",                 "rx", "control"),
        ("RX_CLK",                 "rx", "control")])
    _panel(d, 385, 10, "RMII", [
        ("TXD[1:0], TX_EN",        "tx", "data"),
        ("RXD[1:0], CRS_DV",       "rx", "data")], osc=True)
    _panel(d, 10, PH + 20, "GMII", [
        ("TXD[7:0], TX_EN, TX_ER", "tx", "data"),
        ("RXD[7:0], RX_DV, RX_ER", "rx", "data"),
        ("GTX_CLK",                "tx", "control"),
        ("RX_CLK",                 "rx", "control")])
    _panel(d, 385, PH + 20, "RGMII", [
        ("TXD[3:0], TX_CTL",       "tx", "data"),
        ("RXD[3:0], RX_CTL",       "rx", "data"),
        ("TXC",                    "tx", "control"),
        ("RXC",                    "rx", "control")])
    return d
