#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.common import *

# PCS RX Frame Sync --------------------------------------------------------------------------------

class PCSRXFrameSync(LiteXModule):
    """Block synchronization (Figure 49-12).

    Finds the 66-bit block boundary in the received stream using the sync headers, which are the
    one position in a block guaranteed to contain a transition (49.2.4.3). Every block whose header
    is neither 01 nor 10 is counted as invalid. When too many accumulate, the candidate boundary is
    rejected and the transceiver is asked to slip one bit.
    """
    def __init__(self, hdr_width=2, bitslip_high_cycles=1, bitslip_low_cycles=8):
        # SERDES interface
        self.serdes_rx_hdr     = Signal(hdr_width)
        self.serdes_rx_bitslip = Signal()

        # Status.
        self.rx_block_lock     = Signal()

        bitslip_max   = max(bitslip_high_cycles, bitslip_low_cycles)
        bitslip_width = max(1, bits_for(bitslip_max - 1))

        sh_count         = Signal(6) # 49.2.13.2.4 sh_cnt, one 64-block window
        sh_invalid_count = Signal(4) # 49.2.13.2.4 sh_invalid_cnt, threshold 16
        bitslip_count    = Signal(bitslip_width)

        bitslip    = Signal()
        block_lock = Signal()

        self.comb += [
            self.serdes_rx_bitslip.eq(bitslip),
            self.rx_block_lock.eq(block_lock),
        ]

        # 00 and 11 cannot occur on a correctly aligned stream (49.2.4.6 a)
        hdr_valid = Signal()
        self.comb += hdr_valid.eq((self.serdes_rx_hdr == SYNC_CTRL) |
                                 (self.serdes_rx_hdr == SYNC_DATA))

        self.sync += [
            If(bitslip_count != 0,
                bitslip_count.eq(bitslip_count - 1),
            ).Elif(bitslip,
                bitslip.eq(0),
                bitslip_count.eq(bitslip_low_cycles - 1 if bitslip_low_cycles > 0 else 0),
            ).Elif(hdr_valid,
                # VALID_SH
                sh_count.eq(sh_count + 1),
                If(sh_count == 2**6 - 1,
                    # End of window
                    sh_count.eq(0),
                    sh_invalid_count.eq(0),
                    If(sh_invalid_count == 0,
                        # 64_GOOD: 64 headers, none invalid
                        block_lock.eq(1),
                    ),
                ),
            ).Else(
                # INVALID_SH
                sh_count.eq(sh_count + 1),
                sh_invalid_count.eq(sh_invalid_count + 1),
                If(~block_lock | (sh_invalid_count == 2**4 - 1),
                    # SLIP: unlocked, or the sixteenth invalid header of the window
                    sh_count.eq(0),
                    sh_invalid_count.eq(0),
                    block_lock.eq(0),
                    bitslip.eq(1),
                    bitslip_count.eq(bitslip_high_cycles - 1 if bitslip_high_cycles > 0 else 0),
                ).Elif(sh_count == 2**6 - 1,
                    # End of the window, still locked and under the threshold
                    sh_count.eq(0),
                    sh_invalid_count.eq(0),
                ),
            ),
        ]
