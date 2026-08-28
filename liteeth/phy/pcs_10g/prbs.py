#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

# XXX should this be changed to use the PRBS31 generator / checker in
# litex.soc.cores.prbs?

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.lfsr import fibonacci_lfsr

# Constants ----------------------------------------------------------------------------------------

PRBS31_WIDTH = 31 # State bits, S0..S30 (Figures 49-9 and 49-11).
PRBS31_TAP_0 = 27 # S27, the x^28 term.
PRBS31_TAP_1 = 30 # S30, the x^31 term.

# 49.2.8 requires only that the initial value is not all zeros; all ones matches the reference.
PRBS31_INIT = 2**PRBS31_WIDTH - 1

# PRBS31 Generator ---------------------------------------------------------------------------------

class PRBS31Generator(LiteXModule):
    """PRBS31 test-pattern generator, G(x) = 1 + x^28 + x^31 (IEEE 802.3ae Clause 49.2.8).

    Optional test-pattern mode, selected by cfg_tx_prbs31_enable in the PCS. The generated word
    replaces the whole 66-bit block, sync header included, since in this mode the PCS is driving
    the PMA with a raw bit stream rather than framed blocks.

    Note the output is inverted: 49.2.8 specifies that Figure 49-9 "implements the inverted version
    of the bit stream produced by the polynomial".
    """
    def __init__(self, dw=66):
        self.enable   = Signal()
        self.data_out = Signal(dw)

        self.state = Signal(PRBS31_WIDTH, reset=PRBS31_INIT, reset_less=True)

        # # #

        # The generator has no input: the sequence is driven purely by the state, so the shift
        # register is fed with zeros and takes its feedback from the output bit.
        data_out, state_out = fibonacci_lfsr(self, Constant(0, dw), self.state,
            taps         = (PRBS31_TAP_0, PRBS31_TAP_1),
            feed_forward = False,
        )
        self.comb += self.data_out.eq(~data_out)
        # The state only advances while test-pattern mode is selected, matching the reference:
        # leaving it parked means enabling the mode always restarts from the same point.
        self.sync += If(self.enable, self.state.eq(state_out))

# PRBS31 Checker -----------------------------------------------------------------------------------

class PRBS31Checker(LiteXModule):
    """PRBS31 test-pattern checker (IEEE 802.3ae Clause 49.2.12, Figure 49-11).

    Self-synchronizing: each received bit is compared against what the PRBS31 generator would have
    produced from the prior 31 received bits, so no initialisation in step with the far end is
    required. data_out carries one error flag per received bit, and is all zeros when the received
    stream is error free.

    49.2.12 notes that an isolated bit error raises the error signal three times -- once when it is
    received and once as it passes each tap -- so the error count is three times the bit error
    count for isolated errors.
    """
    def __init__(self, dw=66):
        self.enable   = Signal()
        self.data_in  = Signal(dw)
        self.data_out = Signal(dw)

        self.state = Signal(PRBS31_WIDTH, reset=PRBS31_INIT, reset_less=True)

        # # #

        # Undo the generator's inversion, so an error-free stream reduces to zero.
        data_out, state_out = fibonacci_lfsr(self, ~self.data_in, self.state,
            taps         = (PRBS31_TAP_0, PRBS31_TAP_1),
            feed_forward = True,
        )
        self.comb += self.data_out.eq(data_out)
        self.sync += If(self.enable, self.state.eq(state_out))
