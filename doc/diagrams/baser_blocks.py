#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
#
# BASE-R PHY: 64-bit MAC stream, XGMII adapters, 64b/66b PCS (encode and scramble, block sync and
# BER monitor, descramble and decode) and the device PMA.

from svgdiag import *


def frac(box, y):
    return (y - box.y)/box.h


def build():
    d = Diagram(CANVAS_WIDTH, 270)
    d.add(Container(245, 15, 305, 240, title="PCS", title_pos="br"))
    mac  = d.add(Box(15, 40, 85, 180, label="MAC", fill=COLOR_ACCENT_LIGHT))
    xtx  = d.add(Box(120, 40, 105, 55, label="XGMII TX", fill=COLOR_ACCENT_LIGHT))
    xrx  = d.add(Box(120, 165, 105, 55, label="XGMII RX", fill=COLOR_ACCENT_LIGHT))
    enc  = d.add(Box(265, 40, 275, 55, label="Encode, scramble, PRBS31", fill=COLOR_ACCENT))
    dec  = d.add(Box(265, 165, 125, 55, label="Descramble\ndecode, PRBS31", fill=COLOR_ACCENT))
    sync = d.add(Box(410, 165, 130, 55, label="Block sync\nBER, watchdog", fill=COLOR_ACCENT))
    pma  = d.add(Box(580, 40, 80, 180, label="PMA", fill=COLOR_ACCENT_LIGHT))
    sfp  = d.add(Box(680, 40, 65, 180, label="SFP+", fill=COLOR_HARDWARE))
    yt, yr = 67.5, 192.5
    d.add(Arrow((mac, "r", frac(mac, yt)), (xtx, "l"), kind="data"))
    d.add(Arrow((xtx, "r"), (enc, "l"), kind="data"))
    d.add(Arrow((enc, "r"), (pma, "l", frac(pma, yt)), kind="accent", label="66b", label_pos=(565, 57)))
    d.add(Arrow((pma, "r", frac(pma, yt)), (sfp, "l", frac(sfp, yt)), kind="data"))
    d.add(Arrow((sfp, "l", frac(sfp, yr)), (pma, "r", frac(pma, yr)), kind="data"))
    d.add(Arrow((pma, "l", frac(pma, yr)), (sync, "r"), kind="accent", label="66b", label_pos=(565, 182)))
    d.add(Arrow((sync, "l"), (dec, "r"), kind="data"))
    d.add(Arrow((dec, "l"), (xrx, "r"), kind="data"))
    d.add(Arrow((xrx, "l"), (mac, "r", frac(mac, yr)), kind="data"))
    d.add(Arrow((sync, "t", 0.6), (pma, "l", frac(pma, 130)), kind="feedback", via=[(488, 130)],
        label="bitslip", label_pos=(520, 120)))
    return d
