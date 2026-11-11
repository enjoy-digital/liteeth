#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Standalone UDP core (liteeth_gen, core: udp): the port groups of the generated liteeth_core
# module, with an Etherbone AXI-Lite master and one UDP streamer port.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 285)
    d.add(Container(180, 20, 420, 250, title="liteeth_core", title_pos="bl"))
    mmap = d.add(Box(15, 45, 140, 50, label="mmap (AXI-Lite)", fill=COLOR_ACCENT_LIGHT))
    addr = d.add(Box(15, 105, 140, 50, label="mac_address,\nip_address"))
    pins = d.add(Box(15, 165, 140, 50, label="PHY pins", fill=COLOR_HARDWARE))
    eb   = d.add(Box(200, 45, 100, 50, label="Etherbone"))
    phy  = d.add(Box(200, 165, 100, 50, label="PHY", fill=COLOR_ACCENT_LIGHT))
    core = d.add(Box(330, 45, 110, 170, label="UDP/IP\ncore", fill=COLOR_ACCENT))
    strm = d.add(Box(470, 45, 110, 50, label="Streamer", fill=COLOR_ACCENT_LIGHT))
    user = d.add(Box(625, 45, 120, 50, label="udp0 sink,\nsource"))
    d.add(Arrow((eb, "l"), (mmap, "r"), kind="data"))
    d.add(Arrow((eb, "r"), (330, 70), kind="data", heads="both"))
    d.add(Arrow((addr, "r"), (330, 130), kind="control"))
    d.add(Arrow((pins, "r"), (phy, "l"), kind="data", heads="both"))
    d.add(Arrow((phy, "r"), (330, 190), kind="data", heads="both"))
    d.add(Arrow((440, 70), (strm, "l"), kind="data", heads="both"))
    d.add(Arrow((strm, "r"), (user, "l"), kind="accent", heads="both"))
    return d
