#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

# XGMII Control Characters (Clause 46, Table 46-4) ------------------------------------------------

XGMII_IDLE   = 0x07 # /I/
XGMII_LPI    = 0x06 # /LI/ (low power idle, added by 802.3az)
XGMII_START  = 0xfb # /S/
XGMII_TERM   = 0xfd # /T/
XGMII_ERROR  = 0xfe # /E/
XGMII_SEQ_OS = 0x9c # /Q/ sequence ordered set
XGMII_SIG_OS = 0x5c # /Fsig/ signal ordered set
XGMII_RES_0  = 0x1c
XGMII_RES_1  = 0x3c
XGMII_RES_2  = 0x7c
XGMII_RES_3  = 0xbc
XGMII_RES_4  = 0xdc
XGMII_RES_5  = 0xf7

# 64B/66B Control Codes (Clause 49, Table 49-1) ---------------------------------------------------
# 7-bit control codes carried in the control field of a control block.

CTRL_IDLE  = 0x00
CTRL_LPI   = 0x06
CTRL_ERROR = 0x1e
CTRL_RES_0 = 0x2d
CTRL_RES_1 = 0x33
CTRL_RES_2 = 0x4b
CTRL_RES_3 = 0x55
CTRL_RES_4 = 0x66
CTRL_RES_5 = 0x78

# XGMII control character -> 64B/66B control code (Table 49-1).
#
# Note: /Fsig/ (XGMII_SIG_OS, O code 0xf) is deliberately absent. The reference implementation does
# not encode it either, so an /Fsig/ on XGMII is encoded as /E/ and flagged via tx_bad_block. See
# doc/conformance.md.
CTRL_CODES = {
    XGMII_IDLE  : CTRL_IDLE,
    XGMII_LPI   : CTRL_LPI,
    XGMII_ERROR : CTRL_ERROR,
    XGMII_RES_0 : CTRL_RES_0,
    XGMII_RES_1 : CTRL_RES_1,
    XGMII_RES_2 : CTRL_RES_2,
    XGMII_RES_3 : CTRL_RES_3,
    XGMII_RES_4 : CTRL_RES_4,
    XGMII_RES_5 : CTRL_RES_5,
}

# Ordered Set O Codes (Clause 49, Table 49-1) -----------------------------------------------------

O_SEQ_OS = 0x0
O_SIG_OS = 0xf

# Sync Header (Clause 49.2.4.1) -------------------------------------------------------------------
# The sync header is transmitted least-significant bit first, so as a 2-bit field a data block is
# 0b10 and a control block is 0b01 -- i.e. "01" and "10" on the wire respectively.

SYNC_DATA = 0b10
SYNC_CTRL = 0b01

# Block Type Fields (Clause 49, Figure 49-7) ------------------------------------------------------
# Comments show the lane layout of the 8 XGMII characters the block carries.

BLOCK_TYPE_CTRL     = 0x1e # C7 C6 C5 C4 C3 C2 C1 C0 BT
BLOCK_TYPE_OS_4     = 0x2d # D7 D6 D5 O4 C3 C2 C1 C0 BT
BLOCK_TYPE_START_4  = 0x33 # D7 D6 D5    C3 C2 C1 C0 BT
BLOCK_TYPE_OS_START = 0x66 # D7 D6 D5    O0 D3 D2 D1 BT
BLOCK_TYPE_OS_04    = 0x55 # D7 D6 D5 O4 O0 D3 D2 D1 BT
BLOCK_TYPE_START_0  = 0x78 # D7 D6 D5 D4 D3 D2 D1    BT
BLOCK_TYPE_OS_0     = 0x4b # C7 C6 C5 C4 O0 D3 D2 D1 BT
BLOCK_TYPE_TERM_0   = 0x87 # C7 C6 C5 C4 C3 C2 C1    BT
BLOCK_TYPE_TERM_1   = 0x99 # C7 C6 C5 C4 C3 C2    D0 BT
BLOCK_TYPE_TERM_2   = 0xaa # C7 C6 C5 C4 C3    D1 D0 BT
BLOCK_TYPE_TERM_3   = 0xb4 # C7 C6 C5 C4    D2 D1 D0 BT
BLOCK_TYPE_TERM_4   = 0xcc # C7 C6 C5    D3 D2 D1 D0 BT
BLOCK_TYPE_TERM_5   = 0xd2 # C7 C6    D4 D3 D2 D1 D0 BT
BLOCK_TYPE_TERM_6   = 0xe1 # C7    D5 D4 D3 D2 D1 D0 BT
BLOCK_TYPE_TERM_7   = 0xff #    D6 D5 D4 D3 D2 D1 D0 BT

# All valid block type fields, for validity checking on receive.
BLOCK_TYPES = (
    BLOCK_TYPE_CTRL,
    BLOCK_TYPE_OS_4,
    BLOCK_TYPE_START_4,
    BLOCK_TYPE_OS_START,
    BLOCK_TYPE_OS_04,
    BLOCK_TYPE_START_0,
    BLOCK_TYPE_OS_0,
    BLOCK_TYPE_TERM_0,
    BLOCK_TYPE_TERM_1,
    BLOCK_TYPE_TERM_2,
    BLOCK_TYPE_TERM_3,
    BLOCK_TYPE_TERM_4,
    BLOCK_TYPE_TERM_5,
    BLOCK_TYPE_TERM_6,
    BLOCK_TYPE_TERM_7,
)

# 64B/66B control code -> XGMII control character (the inverse of CTRL_CODES).
XGMII_CHARS = {ctrl : xgmii for xgmii, ctrl in CTRL_CODES.items()}
