#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# LiteEth in a LiteX SoC: add_ethernet maps the MAC's SRAM slots on the CPU bus, add_etherbone adds
# the UDP/IP core with an Etherbone bus master; with_ethmac shares one MAC between both (hybrid).

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 330)
    d.add(Container(10, 15, 630, 300, title="LiteX SoC", title_pos="bl"))
    cpu  = d.add(Box(25, 45, 80, 50, label="CPU"))
    bus  = d.add(Box(130, 45, 80, 150, label="Wishbone\nbus"))
    eb   = d.add(Box(235, 145, 100, 50, label="Etherbone", fill=COLOR_ACCENT))
    core = d.add(Box(360, 145, 100, 50, label="UDP/IP core", fill=COLOR_ACCENT))
    mac  = d.add(Box(485, 45, 75, 150, label="MAC", fill=COLOR_ACCENT))
    phy  = d.add(Box(580, 45, 50, 150, label="PHY", fill=COLOR_ACCENT_LIGHT))
    user = d.add(Box(360, 245, 100, 50, label="User logic"))
    ext  = d.add(Box(665, 95, 85, 50, label="External\nPHY, SFP", fill=COLOR_HARDWARE))
    d.add(Arrow((cpu, "r"), (130, 70), kind="data", heads="both"))
    d.add(Arrow((210, 70), (485, 70), kind="data", heads="both", label="SRAM slots"))
    d.add(Arrow((eb, "l"), (210, 170), kind="data"))
    d.add(Arrow((eb, "r"), (core, "l"), kind="data", heads="both"))
    d.add(Arrow((core, "r"), (485, 170), kind="data", heads="both"))
    d.add(Arrow((560, 120), (580, 120), kind="data", heads="both"))
    d.add(Arrow((630, 120), (ext, "l"), kind="data", heads="both"))
    d.add(Arrow((user, "t"), (core, "b"), kind="accent", heads="both", label="user port", label_side="right"))
    return d
