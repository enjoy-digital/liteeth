#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from functools import reduce
from operator import or_

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.common import *

# XGMII BASE-R Decoder -----------------------------------------------------------------------------

class XGMIIBaseRDecoder(LiteXModule):
    """64B/66B block to XGMII decoder (IEEE 802.3ae Clause 49.2.4).

    Inverse of XGMIIBaseREncoder. A data sync header passes the payload straight through; a control
    sync header selects one of the Figure 49-7 block formats from the block type field, which
    determines how the 64 payload bits map back onto XGMII characters and control bits.
    """
    def __init__(self):
        self.encoded_rx_data   = Signal(64)
        self.encoded_rx_hdr    = Signal(2)

        self.xgmii_rxd         = Signal(64, reset_less=True)
        self.xgmii_rxc         = Signal(8,  reset_less=True)

        self.rx_bad_block      = Signal(reset_less=True)
        self.rx_sequence_error = Signal(reset_less=True)

        data = self.encoded_rx_data
        hdr  = self.encoded_rx_hdr

        # Per-lane control code decoding (Table 49-1) ----------------------------------------------
        # The eight 7-bit control codes sit above the block type field, so lane i is at bit 7i + 8.
        decoded_ctrl = Signal(64)
        decode_err   = Signal(8)

        for i in range(8):
            cases = {ctrl : [
                decoded_ctrl[8*i:8*(i + 1)].eq(char),
                decode_err[i].eq(0),
            ] for ctrl, char in XGMII_CHARS.items()}
            cases["default"] = [
                decoded_ctrl[8*i:8*(i + 1)].eq(XGMII_ERROR),
                decode_err[i].eq(1),
            ]
            self.comb += Case(data[7*i + 8:7*i + 15], cases)

        # Ordered set O code decoding (Table 49-1) -------------------------------------------------
        # DEVIATION FROM verilog-ethernet: the reference recognises only the sequence ordered set
        # /Q/ (O code 0x0) and decodes every other O code, including the signal ordered set /Fsig/
        # (O code 0xF), to /E/ with rx_bad_block asserted. Table 49-1 defines both O codes, and
        # 49.2.4.6 makes a block invalid only if an O code holds a value *not* in that table, so a
        # received /Fsig/ is valid and must be passed through as XGMII 0x5c. 49.2.13.2.3 agrees:
        # R_BLOCK_TYPE classifies block types 0x2d and 0x4b with "a valid O code" as C and 0x66 as
        # S, where "a valid O code is one containing an O code specified in Table 49-1".
        #
        # The transmit path deliberately does *not* mirror this: an Ethernet MAC never emits
        # /Fsig/, so the encoder still rejects it. See doc/conformance.md.
        def decode_o_code(o):
            char  = Signal(8)
            valid = Signal()
            self.comb += [
                char.eq(XGMII_ERROR),
                valid.eq(0),
                If(o == O_SEQ_OS,
                    char.eq(XGMII_SEQ_OS),
                    valid.eq(1),
                ),
                If(o == O_SIG_OS,
                    char.eq(XGMII_SIG_OS),
                    valid.eq(1),
                ),
            ]
            return char, valid

        o0_char, o0_valid = decode_o_code(data[32:36]) # O code for XGMII lane 0.
        o4_char, o4_valid = decode_o_code(data[36:40]) # O code for XGMII lane 4.

        # Block decoding (Figure 49-7) -------------------------------------------------------------
        frame      = Signal()
        frame_next = Signal()

        rxd_next = Signal(64)
        rxc_next = Signal(8)
        bad_next = Signal()
        seq_next = Signal()

        self.comb += [
            rxd_next.eq(Replicate(C(XGMII_ERROR, 8), 8)),
            rxc_next.eq(0xff),
            bad_next.eq(0),
            seq_next.eq(0),
            frame_next.eq(frame),

            # Only the low bit of the sync header is used to pick data vs control, and only the
            # high nibble of the block type to pick the format, both for reduced fan-in. The full
            # header and block type are validated afterwards.
            If(hdr[0] == 0,
                # Data block.
                rxd_next.eq(data),
                rxc_next.eq(0x00),
                bad_next.eq(0),
            ).Else(
                Case(data[4:8], {
                    BLOCK_TYPE_CTRL >> 4 : [
                        # C7 C6 C5 C4 C3 C2 C1 C0 BT
                        rxd_next.eq(decoded_ctrl),
                        rxc_next.eq(0xff),
                        bad_next.eq(decode_err != 0),
                    ],
                    BLOCK_TYPE_OS_4 >> 4 : [
                        # D7 D6 D5 O4 C3 C2 C1 C0 BT
                        rxd_next[0:32].eq(decoded_ctrl[0:32]),
                        rxc_next[0:4].eq(0xf),
                        rxd_next[40:64].eq(data[40:64]),
                        rxc_next[4:8].eq(0x1),
                        rxd_next[32:40].eq(o4_char),
                        If(o4_valid,
                            bad_next.eq(decode_err[0:4] != 0),
                        ).Else(
                            bad_next.eq(1),
                        ),
                    ],
                    BLOCK_TYPE_START_4 >> 4 : [
                        # D7 D6 D5    C3 C2 C1 C0 BT
                        rxd_next.eq(Cat(decoded_ctrl[0:32], C(XGMII_START, 8), data[40:64])),
                        rxc_next.eq(0x1f),
                        bad_next.eq(decode_err[0:4] != 0),
                        seq_next.eq(frame),
                        frame_next.eq(1),
                    ],
                    BLOCK_TYPE_OS_START >> 4 : [
                        # D7 D6 D5    O0 D3 D2 D1 BT
                        rxd_next[8:32].eq(data[8:32]),
                        # Lane 0 carries the ordered set and lanes 1-3 carry data, so only lane 0
                        # is a control character. The reference marks all four as control here,
                        # which is a bug; see doc/conformance.md.
                        rxc_next[0:4].eq(0x1),
                        rxd_next[0:8].eq(o0_char),
                        bad_next.eq(~o0_valid),
                        rxd_next[32:64].eq(Cat(C(XGMII_START, 8), data[40:64])),
                        rxc_next[4:8].eq(0x1),
                        seq_next.eq(frame),
                        frame_next.eq(1),
                    ],
                    BLOCK_TYPE_OS_04 >> 4 : [
                        # D7 D6 D5 O4 O0 D3 D2 D1 BT
                        bad_next.eq(0),
                        rxd_next[8:32].eq(data[8:32]),
                        rxc_next[0:4].eq(0x1),
                        rxd_next[0:8].eq(o0_char),
                        rxd_next[40:64].eq(data[40:64]),
                        rxc_next[4:8].eq(0x1),
                        rxd_next[32:40].eq(o4_char),
                        If(~o0_valid | ~o4_valid,
                            bad_next.eq(1),
                        ),
                    ],
                    BLOCK_TYPE_START_0 >> 4 : [
                        # D7 D6 D5 D4 D3 D2 D1    BT
                        rxd_next.eq(Cat(C(XGMII_START, 8), data[8:64])),
                        rxc_next.eq(0x01),
                        bad_next.eq(0),
                        seq_next.eq(frame),
                        frame_next.eq(1),
                    ],
                    BLOCK_TYPE_OS_0 >> 4 : [
                        # C7 C6 C5 C4 O0 D3 D2 D1 BT
                        rxd_next[8:32].eq(data[8:32]),
                        rxc_next[0:4].eq(0x1),
                        rxd_next[0:8].eq(o0_char),
                        If(o0_valid,
                            bad_next.eq(decode_err[4:8] != 0),
                        ).Else(
                            bad_next.eq(1),
                        ),
                        rxd_next[32:64].eq(decoded_ctrl[32:64]),
                        rxc_next[4:8].eq(0xf),
                    ],
                    BLOCK_TYPE_TERM_0 >> 4 : [
                        # C7 C6 C5 C4 C3 C2 C1    BT
                        rxd_next.eq(Cat(C(XGMII_TERM, 8), decoded_ctrl[8:64])),
                        rxc_next.eq(0xff),
                        bad_next.eq(decode_err[1:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_1 >> 4 : [
                        # C7 C6 C5 C4 C3 C2    D0 BT
                        rxd_next.eq(Cat(data[8:16], C(XGMII_TERM, 8), decoded_ctrl[16:64])),
                        rxc_next.eq(0xfe),
                        bad_next.eq(decode_err[2:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_2 >> 4 : [
                        # C7 C6 C5 C4 C3    D1 D0 BT
                        rxd_next.eq(Cat(data[8:24], C(XGMII_TERM, 8), decoded_ctrl[24:64])),
                        rxc_next.eq(0xfc),
                        bad_next.eq(decode_err[3:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_3 >> 4 : [
                        # C7 C6 C5 C4    D2 D1 D0 BT
                        rxd_next.eq(Cat(data[8:32], C(XGMII_TERM, 8), decoded_ctrl[32:64])),
                        rxc_next.eq(0xf8),
                        bad_next.eq(decode_err[4:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_4 >> 4 : [
                        # C7 C6 C5    D3 D2 D1 D0 BT
                        rxd_next.eq(Cat(data[8:40], C(XGMII_TERM, 8), decoded_ctrl[40:64])),
                        rxc_next.eq(0xf0),
                        bad_next.eq(decode_err[5:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_5 >> 4 : [
                        # C7 C6    D4 D3 D2 D1 D0 BT
                        rxd_next.eq(Cat(data[8:48], C(XGMII_TERM, 8), decoded_ctrl[48:64])),
                        rxc_next.eq(0xe0),
                        bad_next.eq(decode_err[6:8] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_6 >> 4 : [
                        # C7    D5 D4 D3 D2 D1 D0 BT
                        rxd_next.eq(Cat(data[8:56], C(XGMII_TERM, 8), decoded_ctrl[56:64])),
                        rxc_next.eq(0xc0),
                        bad_next.eq(decode_err[7] != 0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    BLOCK_TYPE_TERM_7 >> 4 : [
                        #    D6 D5 D4 D3 D2 D1 D0 BT
                        rxd_next.eq(Cat(data[8:64], C(XGMII_TERM, 8))),
                        rxc_next.eq(0x80),
                        bad_next.eq(0),
                        seq_next.eq(~frame),
                        frame_next.eq(0),
                    ],
                    "default" : [
                        rxd_next.eq(Replicate(C(XGMII_ERROR, 8), 8)),
                        rxc_next.eq(0xff),
                        bad_next.eq(1),
                    ],
                })
            ),

            If(~((hdr == SYNC_DATA) | ((hdr == SYNC_CTRL) &
                 reduce(or_, [data[0:8] == bt for bt in BLOCK_TYPES]))),
                rxd_next.eq(Replicate(C(XGMII_ERROR, 8), 8)),
                rxc_next.eq(0xff),
                bad_next.eq(1),
            ),
        ]

        self.sync += [
            self.xgmii_rxd.eq(rxd_next),
            self.xgmii_rxc.eq(rxc_next),
            self.rx_bad_block.eq(bad_next),
            self.rx_sequence_error.eq(seq_next),
            frame.eq(frame_next),
        ]
