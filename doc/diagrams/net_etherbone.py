#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# An Etherbone packet with one record (32-bit addresses and data), 32 bits per row: packet header,
# record header, writes (base address and data), reads (return address and addresses).

from primer_helpers import *

BIT = 17.5 # px per bit: 32 bits = 560 px.


def build():
    d = Diagram(CANVAS_WIDTH, 275)
    x0, rh = 170, 30
    for bit in (0, 8, 16, 24, 31):
        d.add(Text(x0 + bit*BIT + BIT/2, 15, str(bit), size=11, color="#444444"))
    groups = [
        ("Packet\nheader", COLOR_SHADE, [
            [(16, "Magic 0x4e6f"), (4, "Version"), (4, "Flags"), (4, "Addr."), (4, "Data")],
            [(32, "Padding")]]),
        ("Record\nheader", COLOR_ACCENT_LIGHT, [
            [(8, "Flags"), (8, "Byte enable"), (8, "Write count"), (8, "Read count")]]),
        ("Writes", COLOR_ACCENT, [
            [(32, "Base write address")],
            [(32, "Write data, one word per write")]]),
        ("Reads", COLOR_ACCENT, [
            [(32, "Base return address")],
            [(32, "Read address, one word per read")]]),
    ]
    y = 30
    for name, fill, rows in groups:
        y0 = y
        for r in rows:
            bitfield(d, x0, y, [(bits*BIT, label, fill) for bits, label in r], h=rh, size=13)
            y += rh
        d.add(Text(x0 - 12, (y0 + y)/2, name, size=14, anchor="end"))
        y += 8
    return d
