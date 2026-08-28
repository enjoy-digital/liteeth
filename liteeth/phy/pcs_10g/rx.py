#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from functools import reduce
from operator import add, and_, or_

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.ber_mon import PCSRXBERMonitor
from liteeth.phy.pcs_10g.common import *
from liteeth.phy.pcs_10g.decoder import XGMIIBaseRDecoder
from liteeth.phy.pcs_10g.block_sync import PCSRXFrameSync
from liteeth.phy.pcs_10g.prbs import PRBS31Checker
from liteeth.phy.pcs_10g.scrambler import Descrambler
from liteeth.phy.pcs_10g.watchdog import PCSRXWatchdog

# PCS RX Interface ---------------------------------------------------------------------------------

class PCSRXInterface(LiteXModule):
    """Receive side of the PCS/PMA boundary (IEEE 802.3ae Clause 49.2.9 - 49.2.12)."""

    def __init__(self, dw=64, hdr_width=2, prbs31_enable=False, bitslip_high_cycles=1,
                 bitslip_low_cycles=8, count_125us=195):
        self.encoded_rx_data     = Signal(dw)
        self.encoded_rx_hdr      = Signal(hdr_width)

        self.serdes_rx_data      = Signal(dw)
        self.serdes_rx_hdr       = Signal(hdr_width)
        self.serdes_rx_bitslip   = Signal()
        self.serdes_rx_reset_req = Signal()

        # Status outputs
        self.rx_bad_block        = Signal()
        self.rx_sequence_error   = Signal()
        self.rx_error_count      = Signal(7)
        self.rx_block_lock       = Signal()
        self.rx_high_ber         = Signal()
        self.rx_status           = Signal()

        # Configuration input
        self.cfg_rx_prbs31_enable = Signal()

        # Bit reverse
        data_int = Signal(dw)
        hdr_int  = Signal(hdr_width)
        self.comb += [
            data_int.eq(Cat(*[self.serdes_rx_data[dw - 1 - n]      for n in range(dw)])),
            hdr_int.eq( Cat(*[self.serdes_rx_hdr[hdr_width - 1 - n] for n in range(hdr_width)])),
        ]

        # Descrambler (49.2.10) --------------------------------------------------------------------
        self.descrambler = descrambler = Descrambler(dw)
        self.comb += descrambler.data_in.eq(data_int)

        # Registered
        encoded_data = Signal(dw, reset_less=True)
        encoded_hdr  = Signal(hdr_width, reset_less=True)
        self.sync += [
            encoded_data.eq(descrambler.data_out),
            encoded_hdr.eq(hdr_int),
        ]
        self.comb += [
            self.encoded_rx_data.eq(encoded_data),
            self.encoded_rx_hdr.eq(encoded_hdr),
        ]

        # PRBS31 Checker (49.2.12) -----------------------------------------------------------------
        # The error flags are registered before being counted, and the count itself is pipelined,
        # so rx_error_count trails the block it describes.
        if prbs31_enable:
            self.prbs31 = prbs31 = PRBS31Checker(dw + hdr_width)
            self.comb += [
                prbs31.enable.eq(self.cfg_rx_prbs31_enable),
                prbs31.data_in.eq(Cat(hdr_int, data_int)),
            ]

            errors = Signal(dw + hdr_width, reset_less=True)
            self.sync += If(self.cfg_rx_prbs31_enable,
                errors.eq(prbs31.data_out),
            ).Else(
                errors.eq(0),
            )

            count_odd  = Signal(6)
            count_even = Signal(6)
            count_odd_r  = Signal(6, reset_less=True)
            count_even_r = Signal(6, reset_less=True)
            self.comb += [
                count_odd.eq( reduce(add, [errors[i] for i in range(dw + hdr_width) if i & 1])),
                count_even.eq(reduce(add, [errors[i] for i in range(dw + hdr_width) if not i & 1])),
            ]
            error_count = Signal(7, reset_less=True)
            self.sync += [
                count_odd_r.eq(count_odd),
                count_even_r.eq(count_even),
                error_count.eq(count_odd_r + count_even_r),
            ]
            self.comb += self.rx_error_count.eq(error_count)
        else:
            self.comb += self.rx_error_count.eq(0)

        # Monitors ---------------------------------------------------------------------------------
        self.frame_sync = frame_sync = PCSRXFrameSync(
            hdr_width           = hdr_width,
            bitslip_high_cycles = bitslip_high_cycles,
            bitslip_low_cycles  = bitslip_low_cycles,
        )
        self.ber_mon = ber_mon = PCSRXBERMonitor(hdr_width=hdr_width, count_125us=count_125us)
        self.watchdog = watchdog = PCSRXWatchdog(hdr_width=hdr_width, count_125us=count_125us)

        test_mode = Signal()
        self.comb += test_mode.eq(self.cfg_rx_prbs31_enable if prbs31_enable else 0)

        self.comb += [
            frame_sync.serdes_rx_hdr.eq(hdr_int),
            ber_mon.serdes_rx_hdr.eq(hdr_int),
            watchdog.serdes_rx_hdr.eq(hdr_int),

            self.rx_block_lock.eq(frame_sync.rx_block_lock),
            self.rx_high_ber.eq(ber_mon.rx_high_ber),
            self.rx_status.eq(watchdog.rx_status),

            watchdog.rx_bad_block.eq(self.rx_bad_block),
            watchdog.rx_sequence_error.eq(self.rx_sequence_error),
            watchdog.rx_block_lock.eq(frame_sync.rx_block_lock),
        ]

        # Figure 49-13 holds the BER monitor in BER_MT_INIT without block lock or in test mode, and
        # 49.2.14.1 makes PCS_status depend on hi_ber.
        self.comb += [
            ber_mon.block_lock.eq(frame_sync.rx_block_lock),
            ber_mon.test_mode.eq(test_mode),
            watchdog.rx_high_ber.eq(ber_mon.rx_high_ber),
        ]

        # A PRBS31 stream has no sync headers, so neither bitslipping nor resetting the transceiver
        # makes sense while test-pattern mode is active.
        self.comb += [
            self.serdes_rx_bitslip.eq(frame_sync.serdes_rx_bitslip & ~test_mode),
            self.serdes_rx_reset_req.eq(watchdog.serdes_rx_reset_req & ~test_mode),
        ]

# Receive Process (Figure 49-15) --------------------------------------------------------------------

# R_BLOCK_TYPE values (49.2.13.2.3).
R_TYPE_C = 0
R_TYPE_S = 1
R_TYPE_T = 2
R_TYPE_D = 3
R_TYPE_E = 4

# Receive state machine states (Figure 49-15).
RX_INIT = 0
RX_C    = 1
RX_D    = 2
RX_T    = 3
RX_E    = 4

# LBLOCK_R: "72 bit vector to be sent to the XGMII interface containing two Local Fault
# ordered_sets" (49.2.13.2.3). A Local Fault ordered set is /Q/ followed by 0x00, 0x00, 0x01
# (46.3.4), so lanes 0 and 4 are control and the rest are data.
LBLOCK_R_DATA = 0x0100009c0100009c
LBLOCK_R_CTRL = 0x11

# EBLOCK_R: /E/ in all eight character locations.
EBLOCK_R_DATA = int.from_bytes(bytes([XGMII_ERROR]*8), "little")
EBLOCK_R_CTRL = 0xff

def _r_type(module, data, hdr):
    """Classify a 66-bit block as C, S, T, D or E (49.2.13.2.3, R_BLOCK_TYPE).

    A valid control character is one holding a 10GBASE-R control code from Table 49-1; a valid O
    code is one holding an O code from Table 49-1. Note that only the all-control block excludes
    /E/ from what counts as valid -- the standard says "eight valid control characters other than
    /E/" for block type 0x1e and simply "valid control characters" elsewhere.
    """
    block_type = data[0:8]

    def ctrl_ok(lane):
        field = data[7*lane + 8:7*lane + 15]
        return reduce(or_, [field == code for code in XGMII_CHARS.keys()])

    def ctrl_ok_not_error(lane):
        field = data[7*lane + 8:7*lane + 15]
        return ctrl_ok(lane) & (field != CTRL_ERROR)

    def ctrl_range_ok(lanes):
        return reduce(and_, [ctrl_ok(lane) for lane in lanes])

    o0_valid = (data[32:36] == O_SEQ_OS) | (data[32:36] == O_SIG_OS)
    o4_valid = (data[36:40] == O_SEQ_OS) | (data[36:40] == O_SIG_OS)

    is_ctrl = hdr == SYNC_CTRL
    is_data = hdr == SYNC_DATA

    # Which lanes a terminate block carries as control characters.
    term_types = {
        BLOCK_TYPE_TERM_0 : range(1, 8),
        BLOCK_TYPE_TERM_1 : range(2, 8),
        BLOCK_TYPE_TERM_2 : range(3, 8),
        BLOCK_TYPE_TERM_3 : range(4, 8),
        BLOCK_TYPE_TERM_4 : range(5, 8),
        BLOCK_TYPE_TERM_5 : range(6, 8),
        BLOCK_TYPE_TERM_6 : range(7, 8),
        BLOCK_TYPE_TERM_7 : [],
    }

    type_c = is_ctrl & (
        ((block_type == BLOCK_TYPE_CTRL) & reduce(and_, [ctrl_ok_not_error(i) for i in range(8)])) |
        ((block_type == BLOCK_TYPE_OS_4) & o4_valid & ctrl_range_ok(range(0, 4)))              |
        ((block_type == BLOCK_TYPE_OS_0) & o0_valid & ctrl_range_ok(range(4, 8)))              |
        ((block_type == BLOCK_TYPE_OS_04) & o0_valid & o4_valid)
    )

    type_s = is_ctrl & (
        ((block_type == BLOCK_TYPE_START_4) & ctrl_range_ok(range(0, 4))) |
        ((block_type == BLOCK_TYPE_OS_START) & o0_valid)                  |
         (block_type == BLOCK_TYPE_START_0)
    )

    type_t = is_ctrl & reduce(or_, [
        (block_type == bt) & (ctrl_range_ok(lanes) if lanes else 1)
        for bt, lanes in term_types.items()
    ])

    r_type = Signal(max=R_TYPE_E + 1)
    module.comb += If(is_data,
        r_type.eq(R_TYPE_D),
    ).Elif(type_c,
        r_type.eq(R_TYPE_C),
    ).Elif(type_s,
        r_type.eq(R_TYPE_S),
    ).Elif(type_t,
        r_type.eq(R_TYPE_T),
    ).Else(
        r_type.eq(R_TYPE_E),
    )
    return r_type

# PCS RX -------------------------------------------------------------------------------------------

class PCSRX(LiteXModule):
    """Complete PCS receive path (IEEE 802.3ae Clause 49.2.9 - 49.2.12).

    Transceiver 64b + 2b sync header in, XGMII out. Composes the PCS/PMA receive interface with the
    64B/66B decoder, and implements the receive state machine of Figure 49-15 on top of them.
    """
    def __init__(self, dw=64, hdr_width=2, prbs31_enable=False, bitslip_high_cycles=1,
                 bitslip_low_cycles=8, count_125us=195):
        self.xgmii_rxd           = Signal(dw)
        self.xgmii_rxc           = Signal(dw//8)

        self.serdes_rx_data      = Signal(dw)
        self.serdes_rx_hdr       = Signal(hdr_width)
        self.serdes_rx_bitslip   = Signal()
        self.serdes_rx_reset_req = Signal()

        # Status outputs
        self.rx_error_count      = Signal(7)
        self.rx_bad_block        = Signal()
        self.rx_sequence_error   = Signal()
        self.rx_block_lock       = Signal()
        self.rx_high_ber         = Signal()
        self.rx_status           = Signal()

        # Configuration input
        self.cfg_rx_prbs31_enable = Signal()

        self.interface = interface = PCSRXInterface(
            dw                  = dw,
            hdr_width           = hdr_width,
            prbs31_enable       = prbs31_enable,
            bitslip_high_cycles = bitslip_high_cycles,
            bitslip_low_cycles  = bitslip_low_cycles,
            count_125us         = count_125us,
        )
        self.decoder = decoder = XGMIIBaseRDecoder()

        self.comb += [
            interface.serdes_rx_data.eq(self.serdes_rx_data),
            interface.serdes_rx_hdr.eq(self.serdes_rx_hdr),
            interface.cfg_rx_prbs31_enable.eq(self.cfg_rx_prbs31_enable),

            decoder.encoded_rx_data.eq(interface.encoded_rx_data),
            decoder.encoded_rx_hdr.eq(interface.encoded_rx_hdr),

            # The watchdog judges window quality from the decoder's block error reports.
            interface.rx_bad_block.eq(decoder.rx_bad_block),
            interface.rx_sequence_error.eq(decoder.rx_sequence_error),

            self.serdes_rx_bitslip.eq(interface.serdes_rx_bitslip),
            self.serdes_rx_reset_req.eq(interface.serdes_rx_reset_req),
            self.rx_error_count.eq(interface.rx_error_count),
            self.rx_bad_block.eq(decoder.rx_bad_block),
            self.rx_sequence_error.eq(decoder.rx_sequence_error),
            self.rx_block_lock.eq(interface.rx_block_lock),
            self.rx_high_ber.eq(interface.rx_high_ber),
            self.rx_status.eq(interface.rx_status),
        ]

        # Receive state machine (Figure 49-15) ------------------------------------------------------
        # r_type_next classifies the block at the decoder's input; r_type_cur is the same signal one
        # cycle later, so it classifies the block the decoder is presenting now.
        r_type_next = _r_type(self, interface.encoded_rx_data, interface.encoded_rx_hdr)
        r_type_cur  = Signal(max=R_TYPE_E + 1, reset_less=True)
        self.sync += r_type_cur.eq(r_type_next)

        state      = Signal(max=RX_E + 1, reset=RX_INIT)
        state_next = Signal(max=RX_E + 1)

        # RX_INIT entry condition. reset is covered by the state register's reset value.
        rx_init = Signal()
        self.comb += rx_init.eq(self.rx_high_ber | ~self.rx_block_lock |
            (self.cfg_rx_prbs31_enable if prbs31_enable else 0))

        term_ok = Signal() # a terminate whose successor may legally follow it
        self.comb += term_ok.eq((r_type_cur == R_TYPE_T) &
                                ((r_type_next == R_TYPE_S) | (r_type_next == R_TYPE_C)))

        self.comb += If(rx_init,
            state_next.eq(RX_INIT),
        ).Else(
            Case(state, {
                RX_INIT : If(r_type_cur == R_TYPE_C,
                              state_next.eq(RX_C),
                          ).Elif(r_type_cur == R_TYPE_S,
                              state_next.eq(RX_D),
                          ).Else(
                              state_next.eq(RX_E),
                          ),
                RX_C    : If(r_type_cur == R_TYPE_C,
                              state_next.eq(RX_C),
                          ).Elif(r_type_cur == R_TYPE_S,
                              state_next.eq(RX_D),
                          ).Else(
                              state_next.eq(RX_E),
                          ),
                RX_D    : If(r_type_cur == R_TYPE_D,
                              state_next.eq(RX_D),
                          ).Elif(term_ok,
                              state_next.eq(RX_T),
                          ).Else(
                              state_next.eq(RX_E),
                          ),
                # RX_T is only entered when the next block is already known to be S or C, so those
                # are the only exits Figure 49-15 draws; anything else cannot occur and is treated
                # as an error.
                RX_T    : If(r_type_cur == R_TYPE_C,
                              state_next.eq(RX_C),
                          ).Elif(r_type_cur == R_TYPE_S,
                              state_next.eq(RX_D),
                          ).Else(
                              state_next.eq(RX_E),
                          ),
                RX_E    : If(r_type_cur == R_TYPE_C,
                              state_next.eq(RX_C),
                          ).Elif(r_type_cur == R_TYPE_D,
                              state_next.eq(RX_D),
                          ).Elif(term_ok,
                              state_next.eq(RX_T),
                          ).Else(
                              state_next.eq(RX_E),
                          ),
            }),
        )

        self.sync += state.eq(state_next)

        # State actions. RX_C, RX_D and RX_T pass the decode through; RX_INIT and RX_E substitute.
        self.comb += If(state_next == RX_INIT,
            self.xgmii_rxd.eq(LBLOCK_R_DATA),
            self.xgmii_rxc.eq(LBLOCK_R_CTRL),
        ).Elif(state_next == RX_E,
            self.xgmii_rxd.eq(EBLOCK_R_DATA),
            self.xgmii_rxc.eq(EBLOCK_R_CTRL),
        ).Else(
            self.xgmii_rxd.eq(decoder.xgmii_rxd),
            self.xgmii_rxc.eq(decoder.xgmii_rxc),
        )
