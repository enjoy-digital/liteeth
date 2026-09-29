#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.serial.baser.pcs.common import *

# PCS RX BER Monitor -------------------------------------------------------------------------------

class PCSRXBERMonitor(LiteXModule):
    """BER monitor (IEEE 802.3ae Clause 49.2.13.3, Figure 49-13).

    Defaults to 16 invalid headers per 125 us (Clause 49). Clause 107 uses 97 per 2 ms;
    count_window and threshold select that mode independently of the recovery watchdog.
    """
    def __init__(self, hdr_width=2, count_125us=195, threshold=16, count_window=None):
        if count_window is None:
            count_window = count_125us
        if threshold < 1 or count_window < 1:
            raise ValueError("BER threshold and window must be positive.")
        self.threshold    = threshold
        self.count_window = count_window
        # SERDES interface.
        self.serdes_rx_hdr = Signal(hdr_width)

        # Control inputs (Figure 49-13)
        self.block_lock    = Signal()
        self.test_mode     = Signal()

        # Status outputs
        self.rx_high_ber   = Signal()

        # # #

        count_width = max(1, bits_for(count_window))

        time_count = Signal(count_width, reset=count_window)
        ber_count  = Signal(max=max(threshold, 2)) # Saturates one below the threshold.
        hi_ber     = Signal()

        self.comb += self.rx_high_ber.eq(hi_ber)

        hdr_valid = Signal()
        self.comb += hdr_valid.eq((self.serdes_rx_hdr == SYNC_CTRL) |
                                 (self.serdes_rx_hdr == SYNC_DATA))

        self.sync += If(self.test_mode | ~self.block_lock,
            # BER_MT_INIT.
            hi_ber.eq(0),
            ber_count.eq(0),
            time_count.eq(count_window),
        ).Else(
            If(time_count > 0,
                time_count.eq(time_count - 1),
            ),
            If(hdr_valid,
                # BER_TEST_SH with a valid header: only the window expiring matters.
                If(ber_count != threshold - 1,
                    If(time_count == 0,
                        # GOOD_BER: window ended below the threshold.
                        hi_ber.eq(0),
                    ),
                ),
            ).Else(
                # BER_BAD_SH.
                If(ber_count == threshold - 1,
                    # HI_BER: the threshold invalid header of the window.
                    hi_ber.eq(1),
                ).Else(
                    ber_count.eq(ber_count + 1),
                    If(time_count == 0,
                        hi_ber.eq(0),
                    ),
                ),
            ),
            If(time_count == 0,
                # START_TIMER: restart the window.
                ber_count.eq(0),
                time_count.eq(count_window),
            ),
        )
