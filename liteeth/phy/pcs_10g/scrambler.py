#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.lfsr import fibonacci_lfsr

SCRAMBLER_WIDTH = 58 # State bits, S0..S57 (Figure 49-8).
SCRAMBLER_TAP_0 = 38 # S38, the x^39 term.
SCRAMBLER_TAP_1 = 57 # S57, the x^58 term.


class _ScramblerBase(LiteXModule):
    def __init__(self, dw, feed_forward):
        self.data_in  = Signal(dw)
        self.data_out = Signal(dw)

        # 49.2.6 places no requirement on the initial value; all ones matches the reference. The
        # state is deliberately not reset: 49.2.6 has the scrambler running continuously, the
        # reference resets it nowhere, and clearing it mid-stream would corrupt a block for no
        # benefit -- the descrambler is self-synchronizing and recovers on its own.
        self.state = Signal(SCRAMBLER_WIDTH, reset=2**SCRAMBLER_WIDTH - 1, reset_less=True)

        # # #

        data_out, state_out = fibonacci_lfsr(self, self.data_in, self.state,
            taps         = (SCRAMBLER_TAP_0, SCRAMBLER_TAP_1),
            feed_forward = feed_forward,
        )
        self.comb += self.data_out.eq(data_out)
        self.sync += self.state.eq(state_out)


class Scrambler(_ScramblerBase):
    """Self-synchronizing scrambler, G(x) = 1 + x^39 + x^58 (IEEE 802.3ae Clause 49.2.6).

    Scrambles the 64-bit block payload; the sync header bypasses it (49.2.4.3), so the header is
    not passed through this module. Runs continuously on all payload bits.
    """
    def __init__(self, dw=64):
        _ScramblerBase.__init__(self, dw, feed_forward=False)


class Descrambler(_ScramblerBase):
    """Descrambler for the Clause 49.2.6 scrambler.

    Same shift register, but the received bit rather than the recovered bit is shifted into the
    state, so state is acquired directly from the incoming stream.
    """
    def __init__(self, dw=64):
        _ScramblerBase.__init__(self, dw, feed_forward=True)
