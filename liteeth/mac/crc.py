#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# Copyright (c) 2017 whitequark <whitequark@whitequark.org>
# Copyright (c) 2018 Felix Held <felix-github@felixheld.de>
# SPDX-License-Identifier: BSD-2-Clause

from math import ceil

from litex.gen import *

from liteeth.common import *

from litex.gen.genlib.misc import chooser, WaitTimer

# MAC CRC Engine -----------------------------------------------------------------------------------

class LiteEthMACCRCEngine(LiteXModule):
    """
    Cyclic Redundancy Check (CRC) Engine using an asynchronous LFSR.

    This module calculates the next CRC value based on the previous CRC value and the current data input.

    Parameters
    ----------
    data_width : int
        The bit width of the data bus
    width : int
        The bit width of CRC value.
    polynom : int
        The polynomial used for the CRC calculation, specified as an integer (e.g., 0x04C11DB7 for IEEE 802.3).
    """
    def __init__(self, data_width, width, polynom):
        self.data     = Signal(data_width) # Data (Input).
        self.crc_prev = Signal(width)      # CRC Previous (Input).
        self.crc_next = Signal(width)      # CRC Next (Output).

        # # #

        # Determine bits affected by the polynom.
        polynom_taps = [bit for bit in range(width) if (1 << bit) & polynom]

        # Prepare the list for CRC calculation through LFSR.
        crc_bits = [[("state", i)] for i in range(width)]
        for n in range(data_width):
            feedback = crc_bits.pop(-1) + [("din", n)]
            for pos in range(width - 1):
                if (pos + 1) in polynom_taps:
                    crc_bits[pos] += feedback
                crc_bits[pos] = self.optimize_xors(crc_bits[pos])
            crc_bits.insert(0, feedback)

        # Calculate the next CRC value based on XOR operations.
        for i in range(width):
            xors = []
            for t, n in crc_bits[i]:
                if t == "state":
                    xors += [self.crc_prev[n]]
                elif t == "din":
                    xors += [self.data[n]]
            self.comb += self.crc_next[i].eq(Reduce("XOR", xors))

    @staticmethod
    def optimize_xors(bits):
        """Return items with odd occurrences for XOR optimization."""
        from collections import Counter
        return [bit for bit, count in Counter(bits).items() if count % 2 == 1]

def crc_calc(data_width, width, polynom, crc_prev, data):
    """
    Calculate the next CRC value. Functionally equivalent to the migen CRCEngine, but as a python function

    Parameters
    ----------
    data_width : int
        The bit width of data
    width : int
        The bit width of the CRC value
    polynom : int
        The polynomial used for the CRC calculation, specified as an integer (e.g., 0x04C11DB7 for IEEE 802.3).
    crc_prev : int
        The previous CRC value
    data : int
        The new data word

    Returns
    -------
    int
        The next CRC value
    """
    # Convert crc_prev into a list of bits (LSB first) for easier bitwise operations
    state = [(crc_prev >> i) & 1 for i in range(width)]

    # Process each bit of the input data (assumed LSB-first).
    for n in range(data_width):
        d = (data >> n) & 1
        feedback = state[-1] ^ d
        state.pop()

        # For each remaining bit position (positions 0 .. width-2),
        # if the corresponding tap (at bit position pos+1 in the polynomial)
        # is active, XOR the feedback into that bit.
        for pos in range(width - 1):
            if (polynom >> (pos + 1)) & 1:
                state[pos] ^= feedback
        # Insert the feedback at the beginning of the state (this is equivalent
        # to shifting the register and feeding in the new bit).
        state.insert(0, feedback)

    crc_next = 0
    for i, bit in enumerate(state):
        if bit:
            crc_next |= (1 << i)
    return crc_next

# MAC CRC32 ----------------------------------------------------------------------------------------

@ResetInserter()
@CEInserter()
class LiteEthMACCRC32(LiteXModule):
    """IEEE 802.3 CRC

    Implement an IEEE 802.3 CRC generator/checker.

    Parameters
    ----------
    data_width : int
        Width of the data bus.

    Attributes
    ----------
    data : in
        Data input.
    be : in
        Data byte enable (optional, defaults to full word).
    value : out
        CRC value (used for generator).
    error : out
        CRC error (used for checker).
    """
    width   = 32
    polynom = 0x04c11db7
    init    = 2**width - 1
    check   = 0xc704dd7b
    def __init__(self, data_width):
        self.data  = Signal(data_width)
        self.be    = Signal(data_width//8, reset=2**data_width//8 - 1)
        self.value = Signal(self.width)
        self.error = Signal()

        # # #

        # Create a CRC Engine for each byte segment.
        # Ex for a 32-bit Data-Path, we create 4 engines: 8, 16, 24 and 32-bit engines.
        engines = []
        for n in range(data_width//8):
            engine = LiteEthMACCRCEngine(
                data_width = (n + 1)*8,
                width      = self.width,
                polynom    = self.polynom,
            )
            engines.append(engine)
        self.submodules += engines

        # Register Full-Word CRC Engine (last one).
        reg = Signal(self.width, reset=self.init)
        self.sync += reg.eq(engines[-1].crc_next)

        # Select CRC Engine/Result.
        for n in range(data_width//8):
            self.comb += [
                engines[n].data.eq(self.data),
                engines[n].crc_prev.eq(reg),
                If(self.be[n],
                    self.value.eq(engines[n].crc_next[::-1] ^ self.init),
                    self.error.eq(engines[n].crc_next != self.check),
                )
            ]

# MAC CRC32 ----------------------------------------------------------------------------------------

@ResetInserter()
@CEInserter()
class LiteEthMACCRC32Check(LiteXModule):
    """IEEE 802.3 CRC

    Implement an IEEE 802.3 CRC checker.

    Parameters
    ----------
    data_width : int
        Width of the data bus.

    Attributes
    ----------
    data : in
        Data input.
    be : in
        Data byte enable (optional, defaults to full word).
    error : out
        CRC error (used for checker).
    """
    width   = 32
    polynom = 0x04c11db7
    init    = 2**width - 1
    check   = 0xc704dd7b
    def __init__(self, data_width):
        self.data  = Signal(data_width)
        self.be    = Signal(data_width//8, reset=2**data_width//8 - 1)
        self.value = Signal(self.width)
        self.error = Signal()

        check_be = [self.check]
        for _ in range(1, data_width//8):
            check_be.append(crc_calc(8, self.width, self.polynom, check_be[-1], 0))

        # # #

        # Create a CRC Engine for the data_width
        self.submodules.engine = engine = LiteEthMACCRCEngine(
            data_width = data_width,
            width      = self.width,
            polynom    = self.polynom,
        )

        # Register Full-Word CRC Engine (last one).
        reg = Signal(self.width, reset=self.init)
        self.sync += reg.eq(engine.crc_next)

        # Select CRC Engine/Result.
        self.comb += [
            # TODO mask data
            engine.data.eq(self.data),
            engine.crc_prev.eq(reg),
        ]
        for n in range(data_width//8):
            self.comb += [
                If(self.be[n],
                   engine.data.eq(self.data & (2**((n + 1)*8) - 1)),
                   self.error.eq(engine.crc_next != check_be[-(n + 1)]),
                )
            ]

# MAC CRC32 Inserter -------------------------------------------------------------------------------

class LiteEthMACCRC32Inserter(LiteXModule):
    """CRC Inserter

    Append a CRC at the end of each packet.

    Parameters
    ----------
    description : description
        description of the dataflow.

    Attributes
    ----------
    sink : in
        Packet data without CRC.
    source : out
        Packet data with CRC.
    """
    def __init__(self, description):
        self.sink   = sink   = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        # # #

        # Parameters.
        data_width  = len(sink.data)
        ratio       = 32//data_width
        assert data_width in [8, 32, 64]

        # Signals.
        crc_packet = Signal(32,            reset_less=True)
        last_be    = Signal(data_width//8, reset_less=True)

        # CRC32 Generator.
        self.crc = crc = LiteEthMACCRC32(data_width)
        self.comb += [
            crc.data.eq(sink.data),
            crc.be.eq(sink.last_be),
        ]

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            crc.reset.eq(1),
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                NextState("COPY"),
            )
        )
        fsm.act("COPY",
            crc.ce.eq(sink.valid & source.ready),
            sink.connect(source),
            source.last.eq(0),
            source.last_be.eq(0),
            If(sink.last,
                # Fill the empty space of the last data word with the beginning of the CRC value.
                [If(sink.last_be[e],
                    source.data.eq(Cat(sink.data[:(e+1)*8],
                        crc.value)[:data_width])) for e in range(data_width//8)],
                # If the whole crc value fits in the last sink packet, signal the end. This also
                # means the next state is idle
                If((data_width == 64) & (sink.last_be <= 0xf),
                    source.last.eq(1),
                    source.last_be.eq(sink.last_be << (data_width//8 - 4))
                ),
            ),
            If(sink.valid & sink.last & source.ready,
                If((data_width == 64) & (sink.last_be <= 0xf),
                    NextState("IDLE"),
                ).Else(
                    NextValue(crc_packet, crc.value),
                    If(data_width == 64,
                        NextValue(last_be, sink.last_be >> 4),
                    ).Else (
                        NextValue(last_be, sink.last_be),
                    ),
                    NextState("CRC"),
                )
            )
        )
        if ratio > 1:
            cnt      = Signal(max=ratio, reset=ratio-1)
            cnt_done = Signal()
            fsm.act("CRC",
                source.valid.eq(1),
                chooser(crc_packet, cnt, source.data, reverse=True),
                If(cnt_done,
                    source.last.eq(1),
                    If(source.ready,
                        NextState("IDLE")
                    )
                )
            )
            self.comb += cnt_done.eq(cnt == 0)
            self.sync += \
                If(fsm.ongoing("IDLE"),
                    cnt.eq(cnt.reset)
                ).Elif(fsm.ongoing("CRC") & ~cnt_done,
                    cnt.eq(cnt - source.ready)
                )
        else:
            fsm.act("CRC",
                source.valid.eq(1),
                source.last.eq(1),
                source.data.eq(crc.value),
                source.last_be.eq(last_be),
                [If(last_be[e],
                    source.data.eq(crc_packet[-(e+1)*8:])) for e in range(data_width//8)],
                If(source.ready,
                    NextState("IDLE")
                )
            )

# MAC CRC32 Checker --------------------------------------------------------------------------------

class LiteEthMACCRC32Checker(LiteXModule):
    """CRC Checker

    Check CRC at the end of each packet.

    Parameters
    ----------
    description : description
        description of the dataflow.

    Attributes
    ----------
    sink : in
        Packet data with CRC.
    source : out
        Packet data without CRC and "error" set to 0
        on last when CRC OK / set to 1 when CRC KO.
    error : out
        Pulses every time a CRC error is detected.
    """
    def __init__(self, description):
        self.sink   = sink   = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        self.error = Signal()

        # # #

        # Parameters.
        data_width  = len(sink.data)
        ratio       = ceil(32/data_width)
        assert data_width in [8, 32, 64]

        # CRC32 Checker.
        self.crc = crc = LiteEthMACCRC32Check(data_width)

        # FIFO.
        self.fifo = fifo = ResetInserter()(stream.SyncFIFO(description, ratio + 1))

        fifo_in   = Signal()
        fifo_out  = Signal()
        fifo_full = Signal()

        self.comb += [
            fifo_full.eq(fifo.level == ratio),
            fifo_in.eq(sink.valid & (~fifo_full | fifo_out)),
            fifo_out.eq(source.valid & source.ready),

            sink.connect(fifo.sink),
            fifo.sink.valid.eq(fifo_in),
            self.sink.ready.eq(fifo_in),
        ]

        # FSM.
        self.fsm = fsm = FSM(reset_state="RESET")
        fsm.act("RESET",
            crc.reset.eq(1),
            fifo.reset.eq(1),
            NextState("IDLE"),
        )
        self.comb += [
            crc.data.eq(sink.data),
            crc.be.eq(sink.last_be),
        ]
        fsm.act("IDLE",
            If(sink.valid & sink.ready,
                crc.ce.eq(1),
                NextState("COPY")
            )
        )
        last_be   = Signal().like(sink.last_be)
        crc_error = Signal()
        self.comb += fifo.source.connect(source, omit={"valid", "ready", "last", "last_be"})
        fsm.act("COPY",
            fifo.source.ready.eq(fifo_out),
            source.valid.eq(sink.valid & fifo_full),

            If(data_width <= 32,
                source.last.eq(sink.last),
                source.last_be.eq(sink.last_be),
            # For data_width == 64 bit, we need to look wether the last word contains only the crc value or both crc and data
            # In the latter case, the last word also needs to be output
            # In both cases, last_be needs to be adjusted for the new end position
            ).Elif(sink.last_be & 0xF,
                source.last.eq(sink.last),
                source.last_be.eq(sink.last_be << (data_width//8 - 4)),
            ).Else(
                NextValue(last_be, sink.last_be >> 4),
                NextValue(crc_error, crc.error),
            ),

            # `source.error` has a width > 1 for data_width > 8, but since the crc error
            # applies to the whole ethernet packet, all the bytes are marked as
            # containing an error. This way later reducing the data width
            # doesn't run into issues with missing the error
            source.error.eq(sink.error | Replicate(crc.error & sink.last, data_width//8)),
            self.error.eq(sink.valid & sink.last & crc.error),

            If(sink.valid & sink.ready,
                crc.ce.eq(1),
                # Can only happen for data_width == 64
                If(sink.last & (sink.last_be > 0xF),
                   NextState("COPY_LAST"),
                ).Elif(sink.last,
                    NextState("RESET")
                )
            )
        )

        # If the last sink word contains both data and the crc value, shift out
        # the last value here. Can only happen for data_width == 64
        fsm.act("COPY_LAST",
            fifo.source.connect(source, keep={"valid", "ready", "last"}),
            source.error.eq(fifo.source.error | Replicate(crc_error, data_width//8)),
            source.last_be.eq(last_be),
            If(source.valid & source.ready,
                NextState("RESET")
            )
        )
