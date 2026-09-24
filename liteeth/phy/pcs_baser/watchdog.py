#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_baser.common import *

# PCS RX Watchdog ----------------------------------------------------------------------------------

class PCSRXWatchdog(LiteXModule):
    """Receive watchdog and PCS status.

    rx_status implements PCS_status of 49.2.14.1: "Indicates whether the PCS is in a fully
    operational state. It is only true if block_lock is true and hi_ber is false." It is qualified
    over 16 consecutive good 125 us windows, a window being good if a control sync header was seen
    and the block error count did not saturate.

    serdes_rx_reset_req is not specified in 802.3ae but is inspired by verilog-ethernet: if 16
    consecutive windows are bad, the transceiver is asked to reset, on the theory that the receiver
    has failed in a way bit-slipping cannot fix.

    NOTE: This might benefit from some more parameterization to be useful for 25G.
    """
    def __init__(self, hdr_width=2, count_125us=195):
        self.serdes_rx_hdr       = Signal(hdr_width)
        self.serdes_rx_reset_req = Signal()

        # Monitor inputs
        self.rx_bad_block        = Signal()
        self.rx_sequence_error   = Signal()
        self.rx_block_lock       = Signal()
        self.rx_high_ber         = Signal()

        # Status output
        self.rx_status           = Signal()

        count_width = max(1, bits_for(count_125us))

        time_count        = Signal(count_width, reset=count_125us)
        error_count       = Signal(4)  # consecutive bad windows, 16 triggers a reset request
        status_count      = Signal(4)  # consecutive good windows, 16 qualifies rx_status
        block_error_count = Signal(10) # bad blocks within the window, saturating
        saw_ctrl_sh       = Signal()   # a control sync header was seen this window

        reset_req = Signal()
        status    = Signal()

        self.comb += [
            self.serdes_rx_reset_req.eq(reset_req),
            self.rx_status.eq(status),
        ]

        self.sync += [
            # The reset request is a single-cycle pulse.
            reset_req.eq(0),

            If(self.rx_block_lock,
                If(self.serdes_rx_hdr == SYNC_CTRL,
                    saw_ctrl_sh.eq(1),
                ),
                If((self.rx_bad_block | self.rx_sequence_error) &
                   (block_error_count != 2**10 - 1),
                    block_error_count.eq(block_error_count + 1),
                ),
            ),

            # 49.2.14.1: PCS_status is only true while block_lock holds and hi_ber is clear. The
            # reference omits the hi_ber term; including it also restarts qualification, matching
            # how loss of block lock is handled.
            If(~self.rx_block_lock | self.rx_high_ber,
                status.eq(0),
                status_count.eq(0),
            ),

            If(time_count != 0,
                time_count.eq(time_count - 1),
            ).Else(
                # End of a 125 us window: judge it and start the next.
                time_count.eq(count_125us),
                If(~saw_ctrl_sh | (block_error_count == 2**10 - 1),
                    error_count.eq(error_count + 1),
                    status_count.eq(0),
                ).Else(
                    error_count.eq(0),
                    If(status_count != 2**4 - 1,
                        status_count.eq(status_count + 1),
                    ),
                ),
                If(error_count == 2**4 - 1,
                    error_count.eq(0),
                    reset_req.eq(1),
                ),
                If(status_count == 2**4 - 1,
                    status.eq(1),
                ),
                saw_ctrl_sh.eq(0),
                block_error_count.eq(0),
            ),
        ]
