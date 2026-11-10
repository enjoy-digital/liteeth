#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Getting started: the host scripts reach the board's Wishbone bus through litex_server, UDP and
# the Etherbone frontend.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 245)
    d.add(Container(15, 20, 330, 210, title="Host 192.168.1.100", title_pos="bl"))
    d.add(Container(415, 20, 330, 210, title="Board 192.168.1.50", title_pos="br"))
    script = d.add(Box(35, 50, 110, 50, label="Test scripts"))
    server = d.add(Box(215, 50, 110, 50, label="litex_server", fill=COLOR_ACCENT_LIGHT))
    nic    = d.add(Box(215, 140, 110, 50, label="NIC", fill=COLOR_HARDWARE))
    mem    = d.add(Box(435, 50, 110, 50, label="SRAM,\nregisters"))
    eb     = d.add(Box(615, 50, 110, 50, label="Etherbone", fill=COLOR_ACCENT))
    phy    = d.add(Box(435, 140, 110, 50, label="PHY", fill=COLOR_ACCENT_LIGHT))
    core   = d.add(Box(615, 140, 110, 50, label="UDP/IP core", fill=COLOR_ACCENT))
    d.add(Arrow((script, "r"), (server, "l"), kind="data", heads="both", label="TCP 1234"))
    d.add(Arrow((server, "b"), (nic, "t"), kind="data", heads="both"))
    d.add(Arrow((nic, "r"), (phy, "l"), kind="accent", heads="both", label="UDP 1234"))
    d.add(Arrow((phy, "r"), (core, "l"), kind="data", heads="both"))
    d.add(Arrow((core, "t"), (eb, "b"), kind="data", heads="both"))
    d.add(Arrow((eb, "l"), (mem, "r"), kind="data", heads="both", label="Wishbone"))
    return d
