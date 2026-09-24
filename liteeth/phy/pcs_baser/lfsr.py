#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

# Fibonacci LFSR -----------------------------------------------------------------------------------

def fibonacci_lfsr(module, data_in, state_in, taps, feed_forward):
    """Unroll a two-tap Fibonacci shift register across a data word.

    Shared by the Clause 49 shift registers, which differ only in width, tap positions, and which
    bit is fed back: the 49.2.6 scrambler/descrambler (Figure 49-8) and the 49.2.8/49.2.12 PRBS31
    generator/checker (Figures 49-9 and 49-11). The reference implementation shares one generic
    `lfsr` module between them in the same way.

    Each step XORs the input bit with the two taps to produce an output bit, then shifts one bit
    into S0: the output bit when feed_forward is False, the input bit when it is True. Feeding the
    input bit forward is what makes the descrambler and the PRBS31 checker self-synchronizing --
    both take their state from the received stream rather than needing to be initialised in step
    with the far end.

    Bit 0 of the word is processed first, per the 49.2.4.2 transmission order.

    Returns (data_out, state_out).
    """
    width    = len(state_in)
    state    = [state_in[i] for i in range(width)]
    data_out = []

    for i in range(len(data_in)):
        bit = Signal()
        module.comb += bit.eq(data_in[i] ^ state[taps[0]] ^ state[taps[1]])
        data_out.append(bit)
        state = [data_in[i] if feed_forward else bit] + state[:width - 1]

    return Cat(*data_out), Cat(*state)
