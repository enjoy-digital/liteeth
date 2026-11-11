#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# 1000BASE-X autonegotiation: abilities in /C/ ordered sets, acknowledges, link timer, idles.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 340)
    e = Exchange(d, "LiteEth PCS", "Link partner")
    e.msg("/C/ abilities, ACK = 0", to="right")
    e.msg("/C/ abilities, ACK = 0", to="left")
    e.msg("/C/ abilities, ACK = 1", to="right")
    e.msg("/C/ abilities, ACK = 1", to="left", step=34)
    e.note("link timer", side="left", step=0)
    e.note("link timer", side="right", step=34)
    e.msg("/I/ idles, frames", to="right", kind="accent")
    e.msg("/I/ idles, frames", to="left", kind="accent", step=30)
    e.note("link up", side="left", step=0)
    e.note("link up", side="right")
    return d
