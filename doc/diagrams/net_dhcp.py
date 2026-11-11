#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# A DHCP exchange: DISCOVER, OFFER, REQUEST, ACK between the board and a DHCP server.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 220)
    e = Exchange(d, "Board (client)", "DHCP server", fill_left=COLOR_ACCENT, fill_right=COLOR_HARDWARE)
    e.msg("DISCOVER, broadcast", to="right")
    e.msg("OFFER: address", to="left")
    e.msg("REQUEST, broadcast: address", to="right")
    e.msg("ACK: address, lease time", to="left", kind="accent")
    return d
