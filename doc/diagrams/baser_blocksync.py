#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# BASE-R block synchronization (liteeth/phy/serial/baser/pcs/block_sync.py): headers counted in
# windows of 64, one bit slipped on an invalid header while unlocked or on the sixteenth invalid
# header of a window while locked.

from svgdiag import *


def build():
    d = Diagram(CANVAS_WIDTH, 230)
    slip = d.add(Box(30, 70, 170, 70, label="SLIP\nask for one bit", fill=COLOR_ACCENT_LIGHT))
    hunt = d.add(Box(295, 70, 170, 70, label="UNLOCKED\ncount headers", fill=COLOR_ACCENT_LIGHT))
    lock = d.add(Box(560, 70, 170, 70, label="LOCKED\nblock_lock = 1", fill=COLOR_ACCENT))
    d.add(Mark(lock.x + lock.w - 16, lock.y + 14))
    d.add(Arrow((hunt, "r"), (lock, "l"), kind="data", label="64 valid headers", label_pos=(512, 95)))
    d.add(Arrow((hunt, "l", 0.3), (slip, "r", 0.3), kind="data", label="invalid header", label_pos=(248, 80)))
    d.add(Arrow((slip, "r", 0.7), (hunt, "l", 0.7), kind="feedback", label="hold 8 cycles", label_pos=(248, 130)))
    d.add(Arrow((lock, "t"), (slip, "t"), kind="feedback", via=[(lock.cx, 35), (slip.cx, 35)],
        label="16 invalid in a window of 64", label_pos=(380, 25)))
    d.add(Arrow((lock, "b"), (lock, "b", 0.2), kind="feedback", via=[(lock.cx, 185), (lock.x + 34, 185)],
        label="fewer than 16 invalid", label_pos=(645, 205)))
    return d
