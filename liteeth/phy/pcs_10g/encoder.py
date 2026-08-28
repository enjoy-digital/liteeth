#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.common import *

# XGMII BASE-R Encoder -----------------------------------------------------------------------------

class XGMIIBaseREncoder(LiteXModule):
    """XGMII to 64B/66B block encoder (IEEE 802.3ae Clause 49.2.4).

    Maps one 64-bit XGMII transfer (8 characters + 8 control bits) onto one 66-bit block: a 2-bit
    sync header plus 64 bits of payload. Data-only transfers become a data block (sync header
    0b10); anything containing a control character becomes one of the control block formats of
    Figure 49-7, selected by which lanes hold control characters.
    """
    def __init__(self):
        self.xgmii_txd       = Signal(64)
        self.xgmii_txc       = Signal(8)

        self.encoded_tx_data = Signal(64, reset_less=True)
        self.encoded_tx_hdr  = Signal(2,  reset_less=True)

        self.tx_bad_block    = Signal(reset_less=True)

        txd = self.xgmii_txd
        txc = self.xgmii_txc

        # Per-lane control character encoding (Table 49-1) ----------------------------------------
        # encoded_ctrl holds the 8 x 7-bit control codes; encode_err flags a lane whose character
        # has no control code encoding. A data character in a lane that a block format requires to
        # be a control character is an error too, so lanes with txc low encode as /E/ + error.
        encoded_ctrl = Signal(56)
        encode_err   = Signal(8)

        for i in range(8):
            cases = {code : [
                encoded_ctrl[7*i:7*(i + 1)].eq(ctrl),
                encode_err[i].eq(0),
            ] for code, ctrl in CTRL_CODES.items()}
            cases["default"] = [
                encoded_ctrl[7*i:7*(i + 1)].eq(CTRL_ERROR),
                encode_err[i].eq(1),
            ]
            self.comb += If(txc[i],
                Case(txd[8*i:8*(i + 1)], cases)
            ).Else(
                encoded_ctrl[7*i:7*(i + 1)].eq(CTRL_ERROR),
                encode_err[i].eq(1),
            )

        # Block encoding (Figure 49-7) -------------------------------------------------------------
        # Concatenations are little-endian (Cat puts its first argument in the low bits), so each
        # block is built block-type-field first. The zero fields are the unused bits the figure
        # requires to be sent as zero in the terminate and start blocks.
        data = Signal(64)
        hdr  = Signal(2)
        bad  = Signal()

        self.comb += If(txc == 0x00,
            # Data block: all eight lanes carry data characters.
            data.eq(txd),
            hdr.eq(SYNC_DATA),
            bad.eq(0),
        ).Else(
            hdr.eq(SYNC_CTRL),
            If((txc == 0x1f) & (txd[32:40] == XGMII_SEQ_OS),
                # Ordered set in lane 4.
                data.eq(Cat(C(BLOCK_TYPE_OS_4, 8), encoded_ctrl[0:28], C(O_SEQ_OS, 4), txd[40:64])),
                bad.eq(encode_err[0:4] != 0),
            ).Elif((txc == 0x1f) & (txd[32:40] == XGMII_START),
                # Start in lane 4.
                data.eq(Cat(C(BLOCK_TYPE_START_4, 8), encoded_ctrl[0:28], C(0, 4), txd[40:64])),
                bad.eq(encode_err[0:4] != 0),
            ).Elif((txc == 0x11) & (txd[0:8] == XGMII_SEQ_OS) & (txd[32:40] == XGMII_START),
                # Ordered set in lane 0, start in lane 4.
                data.eq(Cat(C(BLOCK_TYPE_OS_START, 8), txd[8:32], C(O_SEQ_OS, 4), C(0, 4), txd[40:64])),
                bad.eq(0),
            ).Elif((txc == 0x11) & (txd[0:8] == XGMII_SEQ_OS) & (txd[32:40] == XGMII_SEQ_OS),
                # Ordered set in lane 0 and lane 4.
                data.eq(Cat(C(BLOCK_TYPE_OS_04, 8), txd[8:32], C(O_SEQ_OS, 4), C(O_SEQ_OS, 4), txd[40:64])),
                bad.eq(0),
            ).Elif((txc == 0x01) & (txd[0:8] == XGMII_START),
                # Start in lane 0.
                data.eq(Cat(C(BLOCK_TYPE_START_0, 8), txd[8:64])),
                bad.eq(0),
            ).Elif((txc == 0xf1) & (txd[0:8] == XGMII_SEQ_OS),
                # Ordered set in lane 0.
                data.eq(Cat(C(BLOCK_TYPE_OS_0, 8), txd[8:32], C(O_SEQ_OS, 4), encoded_ctrl[28:56])),
                bad.eq(encode_err[4:8] != 0),
            ).Elif((txc == 0xff) & (txd[0:8] == XGMII_TERM),
                # Terminate in lane 0.
                data.eq(Cat(C(BLOCK_TYPE_TERM_0, 8), C(0, 7), encoded_ctrl[7:56])),
                bad.eq(encode_err[1:8] != 0),
            ).Elif((txc == 0xfe) & (txd[8:16] == XGMII_TERM),
                # Terminate in lane 1.
                data.eq(Cat(C(BLOCK_TYPE_TERM_1, 8), txd[0:8], C(0, 6), encoded_ctrl[14:56])),
                bad.eq(encode_err[2:8] != 0),
            ).Elif((txc == 0xfc) & (txd[16:24] == XGMII_TERM),
                # Terminate in lane 2.
                data.eq(Cat(C(BLOCK_TYPE_TERM_2, 8), txd[0:16], C(0, 5), encoded_ctrl[21:56])),
                bad.eq(encode_err[3:8] != 0),
            ).Elif((txc == 0xf8) & (txd[24:32] == XGMII_TERM),
                # Terminate in lane 3.
                data.eq(Cat(C(BLOCK_TYPE_TERM_3, 8), txd[0:24], C(0, 4), encoded_ctrl[28:56])),
                bad.eq(encode_err[4:8] != 0),
            ).Elif((txc == 0xf0) & (txd[32:40] == XGMII_TERM),
                # Terminate in lane 4.
                data.eq(Cat(C(BLOCK_TYPE_TERM_4, 8), txd[0:32], C(0, 3), encoded_ctrl[35:56])),
                bad.eq(encode_err[5:8] != 0),
            ).Elif((txc == 0xe0) & (txd[40:48] == XGMII_TERM),
                # Terminate in lane 5.
                data.eq(Cat(C(BLOCK_TYPE_TERM_5, 8), txd[0:40], C(0, 2), encoded_ctrl[42:56])),
                bad.eq(encode_err[6:8] != 0),
            ).Elif((txc == 0xc0) & (txd[48:56] == XGMII_TERM),
                # Terminate in lane 6.
                data.eq(Cat(C(BLOCK_TYPE_TERM_6, 8), txd[0:48], C(0, 1), encoded_ctrl[49:56])),
                bad.eq(encode_err[7] != 0),
            ).Elif((txc == 0x80) & (txd[56:64] == XGMII_TERM),
                # Terminate in lane 7.
                data.eq(Cat(C(BLOCK_TYPE_TERM_7, 8), txd[0:56])),
                bad.eq(0),
            ).Elif(txc == 0xff,
                # Control block: all eight lanes carry control characters.
                data.eq(Cat(C(BLOCK_TYPE_CTRL, 8), encoded_ctrl)),
                bad.eq(encode_err != 0),
            ).Else(
                # No corresponding block format.
                data.eq(Cat(C(BLOCK_TYPE_CTRL, 8), Replicate(C(CTRL_ERROR, 7), 8))),
                bad.eq(1),
            )
        )

        self.sync += [
            self.encoded_tx_data.eq(data),
            self.encoded_tx_hdr.eq(hdr),
            self.tx_bad_block.eq(bad),
        ]
