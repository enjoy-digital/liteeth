#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Simulation: a unit test drives the core under test from a Python packet streamer and logger,
# with the Python models of test/model as the PHY and the far end of the link.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 260)
    d.add(Container(15, 20, 170, 220, title="Test", title_pos="bl"))
    d.add(Container(205, 20, 170, 220, title="Gateware", title_pos="bl"))
    d.add(Container(395, 20, 350, 220, title="Python models", title_pos="br"))
    streamer = d.add(Box(35, 50, 130, 45, label="Streamer"))
    logger   = d.add(Box(35, 150, 130, 45, label="Logger"))
    core     = d.add(Box(225, 50, 130, 145, label="UDP/IP core", fill=COLOR_ACCENT))
    phy      = d.add(Box(415, 50, 125, 45, label="PHY model", fill=COLOR_ACCENT_LIGHT))
    mac      = d.add(Box(610, 50, 115, 45, label="MAC model"))
    ip       = d.add(Box(610, 150, 115, 45, label="ARP, IP models"))
    udp      = d.add(Box(415, 150, 125, 45, label="UDP loopback"))
    d.add(Arrow((streamer, "r"), (core, "l", 22.5/145), kind="data"))
    d.add(Arrow((core, "l", 122.5/145), (logger, "r"), kind="data"))
    d.add(Arrow((core, "r", 22.5/145), (phy, "l"), kind="accent", heads="both"))
    d.add(Arrow((phy, "r"), (mac, "l"), kind="data", heads="both", label="frames"))
    d.add(Arrow((mac, "b"), (ip, "t"), kind="data", heads="both"))
    d.add(Arrow((ip, "l"), (udp, "r"), kind="data", heads="both", label="packets"))
    return d
