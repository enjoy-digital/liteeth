#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# RGMII at 1 Gbps: data and control on both clock edges, receiver clock delayed by about 2 ns.

from primer_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 230)
    x0, period, n = 150, 150, 3
    half = period/2
    # Clock at the transmitter.
    row_name(d, x0, 40, "Clock (TXC)")
    clock(d, x0, 40, period, n, phase=0.25)
    t0 = x0 + 0.25*period
    # Data and control: change on both edges.
    row_name(d, x0, 90, "TXD[3:0]")
    row_name(d, x0, 130, "TX_CTL")
    cells_d, cells_c = [], []
    for i in range(2*n):
        lo = (i % 2) == 0
        d.add(Box(t0 + i*half, 77, half, 26, radius=0, size=13,
            label=("bits 3:0" if lo else "bits 7:4"), fill=COLOR_ACCENT if lo else COLOR_ACCENT_LIGHT))
        d.add(Box(t0 + i*half, 117, half, 26, radius=0, size=13,
            label=("EN" if lo else "EN xor ER"), fill=COLOR_ACCENT if lo else COLOR_ACCENT_LIGHT))
    # Clock delayed by a quarter period at the receiver.
    row_name(d, x0, 180, "Delayed clock")
    clock(d, x0, 180, period, n, phase=0.5)
    for i in range(2*n + 1):
        tick(d, t0 + i*half, 60, 150)
    tq = t0 + 0.25*period
    d.add(Arrow((t0, 210), (tq, 210), kind="control", heads="both"))
    d.add(Text(tq + 10, 210, "about 2 ns", size=12, anchor="start"))
    return d
