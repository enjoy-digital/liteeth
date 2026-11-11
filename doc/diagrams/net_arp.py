#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# An ARP exchange: broadcast request from the host, unicast reply from the board, then IP traffic.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 180)
    e = Exchange(d, "Board (LiteEth)", "Host", fill_left=COLOR_ACCENT, fill_right=COLOR_HARDWARE)
    e.msg("Request, broadcast: who has 192.168.1.50?", to="left")
    e.msg("Reply, unicast: 192.168.1.50 is at 10:e2:d5:00:00:00", to="right")
    e.msg("IP packets", to="left", kind="accent")
    return d
