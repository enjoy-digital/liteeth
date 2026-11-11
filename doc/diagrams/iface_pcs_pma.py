#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# A serial Ethernet link: MAC and PCS in the fabric, the transceiver (PMA) serializes and recovers
# clock and data, the SFP module drives the fibre or the cable.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 300)
    d.add(Container(10, 10, 515, 215, title="FPGA", title_pos="bl"))
    d.add(Container(275, 25, 235, 160, title="Transceiver", title_pos="tr", title_size=15, fill=COLOR_FILL))
    mac = d.add(Box(25, 50, 75, 110, label="MAC", fill=COLOR_ACCENT_LIGHT))
    pcs = d.add(Box(130, 50, 110, 110, label="PCS\n(fabric)", fill=COLOR_ACCENT))
    ser = d.add(Box(290, 60, 130, 40, label="Serializer", size=14, fill=COLOR_ACCENT_LIGHT))
    des = d.add(Box(290, 125, 130, 50, label="CDR and\ndeserializer", size=14, fill=COLOR_ACCENT_LIGHT))
    pll = d.add(Box(440, 95, 55, 40, label="PLL", size=14, fill=COLOR_ACCENT_LIGHT))
    sfp = d.add(Box(555, 50, 85, 110, label="SFP\nmodule", fill=COLOR_HARDWARE))
    lp  = d.add(Box(670, 50, 80, 110, label="Link\npartner", fill=COLOR_HARDWARE))
    ref = d.add(Box(405, 245, 125, 40, label="Reference clock", size=14, fill=COLOR_HARDWARE))
    d.add(Arrow((mac.x + mac.w, 80), (pcs.x, 80), kind="data"))
    d.add(Arrow((pcs.x, 140), (mac.x + mac.w, 140), kind="data"))
    d.add(Arrow((pcs.x + pcs.w, 80), (ser.x, 80), kind="data"))
    d.add(Arrow((des.x, 140), (pcs.x + pcs.w, 140), kind="data"))
    d.add(Arrow((ser.x + ser.w, 80), (sfp.x, 80), kind="accent"))
    d.add(Arrow((sfp.x, 140), (des.x + des.w, 140), kind="accent"))
    d.add(Arrow((sfp, "r"), (lp, "l"), kind="data", heads="both"))
    d.add(Arrow((pll.cx, ref.y), (pll, "b"), kind="control"))
    d.add(Arrow((pll.x, 98), (ser.x + ser.w, 98), kind="feedback"))
    d.add(Arrow((pll.x, 130), (des.x + des.w, 130), kind="feedback"))
    return d
