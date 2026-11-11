#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# PTP end-to-end delay mechanism: Sync (t1, t2), Follow_Up, Delay_Req (t3, t4), Delay_Resp.

from primer_helpers import *


def _t(d, e, y, left, right):
    d.add(Text(e.xl - 12, y, left, size=14, anchor="end"))
    d.add(Text(e.xr + 12, y, right, size=14, anchor="start"))


def build():
    d = Diagram(CANVAS_WIDTH, 220)
    e = Exchange(d, "Slave (LiteEth)", "Master", fill_left=COLOR_ACCENT, fill_right=COLOR_HARDWARE)
    y = e.msg("Sync", to="left", kind="accent")
    _t(d, e, y, "t2", "t1")
    e.msg("Follow_Up: t1", to="left")
    y = e.msg("Delay_Req", to="right", kind="accent")
    _t(d, e, y, "t3", "t4")
    e.msg("Delay_Resp: t4", to="left")
    return d
