#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 1000BASE-X / 2500BASE-X / SGMII PHY: a wrapper joins the shared 8b/10b PCS to a device PMA that
# drives the transceiver; the external side is an SFP module or an SGMII PHY.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 230)
    d.add(Container(135, 15, 410, 200, title="Wrapper", title_pos="tr"))
    mac  = d.add(Box(15, 60, 95, 120, label="MAC", fill=COLOR_ACCENT_LIGHT))
    pcs  = d.add(Box(155, 60, 165, 120, label="PCS\n8b/10b\nautonegotiation", fill=COLOR_ACCENT))
    pma  = d.add(Box(360, 60, 165, 120, label="PMA\ntransceiver or I/Os\nclocks, init, reset", fill=COLOR_ACCENT_LIGHT))
    ext  = d.add(Box(575, 60, 170, 120, label="SFP module\nor SGMII PHY", fill=COLOR_HARDWARE))
    d.add(Arrow((mac, "r", 0.25), (pcs, "l", 0.25), kind="data"))
    d.add(Arrow((pcs, "l", 0.75), (mac, "r", 0.75), kind="data"))
    d.add(Arrow((pcs, "r", 0.25), (pma, "l", 0.25), kind="accent", label="10-bit", label_pos=(340, 80)))
    d.add(Arrow((pma, "l", 0.75), (pcs, "r", 0.75), kind="accent"))
    d.add(Arrow((pma, "r", 0.25), (ext, "l", 0.25), kind="data"))
    d.add(Arrow((ext, "l", 0.75), (pma, "r", 0.75), kind="data"))
    return d
