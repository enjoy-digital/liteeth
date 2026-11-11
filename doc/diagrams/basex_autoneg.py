#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# BASE-X PCS autonegotiation state machine (liteeth/phy/serial/basex/pcs.py): break link, wait for
# the partner ability, wait for its acknowledge, send more acknowledges, run.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 215)
    w, h, y = 125, 60, 55
    xs = [15, 165, 315, 465, 620]
    labels = ["BREAKLINK\nempty config", "WAIT-ABI\nsend config", "WAIT-ACK\nsend ack",
              "SEND-MORE-ACK\nsend ack", "RUNNING\nlink up"]
    fills  = [COLOR_FILL, COLOR_ACCENT_LIGHT, COLOR_ACCENT_LIGHT, COLOR_ACCENT_LIGHT, COLOR_ACCENT]
    boxes  = [d.add(Box(x, y, w, h, label=l, fill=f, size=13)) for x, l, f in zip(xs, labels, fills)]
    steps  = ["10 ms", "ability", "ack", "timer"]
    for a, b, s in zip(boxes, boxes[1:], steps):
        d.add(Arrow((a, "r"), (b, "l"), kind="data", label=s, label_pos=((a.x + a.w + b.x)/2, y - 15)))
    d.add(Mark(boxes[4].x + boxes[4].w - 14, y + 12))
    # Restart paths back to BREAKLINK.
    yb = 175
    d.add(Arrow((boxes[4], "b", 0.5), (boxes[0], "b", 0.5), kind="feedback",
        via=[(boxes[4].cx, yb), (boxes[0].cx, yb)],
        label="no valid /C/ or /I/ in 6 ms, link down or partner restart", label_pos=(380, 195)))
    for b in boxes[1:4]:
        d.add(Arrow((b, "b", 0.5), (b.cx, yb), kind="feedback", heads="none"))
    return d
