#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# IPv4 header (20 bytes, no options) and UDP header (8 bytes), 32 bits per row.

from primer_helpers import *

BIT = 17.5 # px per bit: 32 bits = 560 px.


def _row(d, x, y, fields, fill):
    bitfield(d, x, y, [(bits*BIT, label, fill) for bits, label in fields], h=30, size=13)


def build():
    d = Diagram(CANVAS_WIDTH, 330)
    x0, y, rh = 150, 30, 30
    for bit in (0, 8, 16, 24, 31):
        d.add(Text(x0 + bit*BIT + BIT/2, 15, str(bit), size=11, color="#444444"))
    ip = COLOR_ACCENT_LIGHT
    rows = [
        [(4, "Version"), (4, "IHL"), (8, "DSCP, ECN"), (16, "Total length")],
        [(16, "Identification"), (3, "Flags"), (13, "Fragment offset")],
        [(8, "TTL"), (8, "Protocol"), (16, "Header checksum")],
        [(32, "Source address")],
        [(32, "Destination address")],
    ]
    for r in rows:
        _row(d, x0, y, r, ip)
        y += rh
    d.add(Text(x0 - 12, 30 + 2.5*rh, "IPv4\n20 bytes", size=14, anchor="end"))
    y += 15
    yu = y
    for r in ([(16, "Source port"), (16, "Destination port")], [(16, "Length"), (16, "Checksum")]):
        _row(d, x0, y, r, COLOR_ACCENT)
        y += rh
    d.add(Text(x0 - 12, yu + rh, "UDP\n8 bytes", size=14, anchor="end"))
    y += 15
    bitfield(d, x0, y, [(32*BIT, "Payload", COLOR_FILL)], h=40, size=14)
    return d
