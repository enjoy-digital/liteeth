#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# UDP streamer: TX FIFO and destination latch, RX filter and FIFO, shared settings.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 280)
    w, h = 150, 54
    user = d.add(Box(10, 30, 100, 230, label="User\nlogic"))
    xbar = d.add(Box(640, 30, 110, 230, label="UDP\ncrossbar"))
    fifo = d.add(Box(160, 40,  w, h, label="TX packet FIFO", fill=COLOR_ACCENT))
    dst  = d.add(Box(400, 40,  w, h, label="Destination\nlatch", fill=COLOR_ACCENT))
    cfg  = d.add(Box(400, 123, w, 40, label="enable, IP, port", fill=COLOR_ACCENT_LIGHT, size=13))
    filt = d.add(Box(400, 195, w, h, label="Port and IP\nfilter", fill=COLOR_ACCENT))
    rxf  = d.add(Box(160, 195, w, h, label="RX FIFO", fill=COLOR_ACCENT))
    d.add(Arrow((user.x + user.w, fifo.cy), (fifo, "l"), kind="accent", label="sink",
        label_pos=(135, fifo.cy - 10)))
    d.add(Arrow((fifo, "r"), (dst, "l"), kind="accent", label="packets",
        label_pos=(355, fifo.cy - 10)))
    d.add(Arrow((dst, "r"), (xbar.x, dst.cy), kind="accent"))
    d.add(Arrow((xbar.x, filt.cy), (filt, "r"), kind="accent"))
    d.add(Arrow((filt, "l"), (rxf, "r"), kind="accent", label="payload",
        label_pos=(355, filt.cy - 10)))
    d.add(Arrow((rxf, "l"), (user.x + user.w, rxf.cy), kind="accent", label="source",
        label_pos=(135, rxf.cy - 10)))
    d.add(Arrow((cfg, "t"), (dst, "b"), kind="control"))
    d.add(Arrow((cfg, "b"), (filt, "t"), kind="control"))
    return d
