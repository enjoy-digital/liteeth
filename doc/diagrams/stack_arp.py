#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# ARP table state machine: replies, cache lookups, requests with retries, responses to IP TX.

from arch_helpers import *


def build():
    d = Diagram(CANVAS_WIDTH, 410)
    w = 150
    rep   = state(d, 20,  30,  "SEND_REPLY", w=w)
    upd   = state(d, 20,  140, "UPDATE_TABLE", w=w)
    idle  = state(d, 305, 140, "IDLE", w=w, fill=COLOR_ACCENT)
    chk   = state(d, 590, 30,  "CHECK_TABLE", w=w, fill=COLOR_ACCENT_LIGHT)
    sreq  = state(d, 590, 140, "SEND_REQUEST", w=w, fill=COLOR_ACCENT_LIGHT)
    creq  = state(d, 590, 250, "CHECK_REQUEST", w=w, fill=COLOR_ACCENT_LIGHT)
    pres  = state(d, 290, 330, "PRESENT_RESPONSE", w=180, fill=COLOR_ACCENT)
    d.add(Arrow((330, idle.y), (rep, "r"), via=[(330, rep.cy)], heads="both", label="request in",
        label_pos=(250, rep.cy - 9)))
    d.add(Arrow((idle.x, idle.cy - 8), (upd.x + upd.w, idle.cy - 8), label="reply in",
        label_pos=(237, idle.cy - 17)))
    d.add(Arrow((upd.x + upd.w, idle.cy + 8), (idle.x, idle.cy + 8), label="other IP",
        label_pos=(237, idle.cy + 22)))
    d.add(Arrow((upd, "b"), (pres, "l"), via=[(95, pres.cy)], label="pending IP",
        label_pos=(200, pres.cy - 9)))
    d.add(Arrow((430, idle.y), (chk, "l"), via=[(430, chk.cy)], label="lookup",
        label_pos=(510, chk.cy - 9)))
    d.add(Arrow((chk, "b"), (sreq, "t"), label="miss", label_pos=(673, 112), label_anchor="start"))
    d.add(Arrow((sreq, "l"), (idle, "r"), label="sent", label_pos=(522, idle.cy - 9)))
    d.add(Arrow((430, idle.y + idle.h), (creq, "l"), via=[(430, creq.cy)], label="every 100 ms",
        label_pos=(510, creq.cy - 9)))
    d.add(Arrow((creq, "t"), (sreq, "b"), label="retry", label_pos=(673, 222), label_anchor="start"))
    d.add(Arrow((640, creq.y + creq.h), (pres, "r"), via=[(640, pres.cy)], label="8 requests",
        label_pos=(540, pres.cy - 9)))
    d.add(Arrow((chk, "r"), (380, pres.y + pres.h), via=[(752, chk.cy), (752, 395), (380, 395)],
        label="hit", label_pos=(744, 200), label_anchor="end"))
    d.add(Arrow((380, pres.y), (380, idle.y + idle.h), label="response", label_pos=(372, 260), label_anchor="end"))
    return d
