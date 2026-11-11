#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# Simulation: the Verilator SoC of litex_sim or bench/sim.py talks to the Linux network stack of
# the host through the ethernet simulation module and a TAP interface.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 245)
    d.add(Container(15, 20, 400, 210, title="Verilator simulation", title_pos="bl"))
    d.add(Container(435, 20, 310, 210, title="Host", title_pos="br"))
    soc    = d.add(Box(35, 50, 140, 50, label="Etherbone,\nCPU", fill=COLOR_ACCENT_LIGHT))
    core   = d.add(Box(35, 140, 140, 50, label="Core, MAC", fill=COLOR_ACCENT))
    phy    = d.add(Box(255, 140, 140, 50, label="PHY model", fill=COLOR_ACCENT))
    mod    = d.add(Box(255, 50, 140, 50, label="ethernet\nmodule"))
    tap    = d.add(Box(455, 50, 120, 50, label="tap0", fill=COLOR_HARDWARE))
    tools  = d.add(Box(605, 50, 120, 50, label="ping,\nlitex_server"))
    stack  = d.add(Box(455, 140, 120, 50, label="Linux stack"))
    d.add(Arrow((soc, "b"), (core, "t"), kind="data", heads="both"))
    d.add(Arrow((core, "r"), (phy, "l"), kind="data", heads="both"))
    d.add(Arrow((phy, "t"), (mod, "b"), kind="accent", heads="both", label="stream", label_side="right"))
    d.add(Arrow((mod, "r"), (tap, "l"), kind="accent", heads="both"))
    d.add(Arrow((tap, "b"), (stack, "t"), kind="data", heads="both"))
    d.add(Arrow((stack, "r"), (tools, "b"), kind="data", heads="both", via=[(665, 165)]))
    return d
