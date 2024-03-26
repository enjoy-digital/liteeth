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
    The CRC calculation is optimized for speed and resource efficiency.

    Parameters
    ----------
    width : int
        The bit width of the data bus and CRC value.
    polynom : int
        The polynomial used for the CRC calculation, specified as an integer (e.g., 0x04C11DB7 for IEEE 802.3).
    """
    def __init__(self, data_width, width, polynom):
        self.data     = Signal(data_width)
        self.crc_prev = Signal(width)
        self.crc_next = Signal(width)

        # # #

        # compute and optimize the parallel implementation of the CRC's LFSR
        taps = [x for x in range(width) if (1 << x) & polynom]
        curval = [[("state", i)] for i in range(width)]
        for i in range(data_width):
            feedback = curval.pop() + [("din", i)]
            for j in range(width-1):
                if j+1 in taps:
                    curval[j] += feedback
                curval[j] = self.optimize_xors(curval[j])
            curval.insert(0, feedback)

        # implement logic
        for i in range(width):
            xors = []
            for t, n in curval[i]:
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
    last_be : in
        Valid byte in data input (optional).
    value : out
        CRC value (used for generator).
    error : out
        CRC error (used for checker).
    """
    width   = 32
    polynom = 0x04C11DB7
    init    = 2**width-1
    check   = 0xC704DD7B
    def __init__(self, data_width):
        dw = data_width//8

        self.data  = Signal(data_width)
        self.last_be = Signal(dw)
        self.value = Signal(self.width)
        self.error = Signal()
        # Add a separate last_be signal, to maintain backwards compatability
        last_be = Signal(data_width//8)

        # # #

        self.comb += [
            If(self.last_be != 0,
                last_be.eq(self.last_be)
            ).Else(
                last_be.eq(2**(dw-1)))
        ]
        # Since the data can end at any byte end, indicated by `last_be`
        # maintain separate engines for each 8 byte increment in the data word
        engines = [LiteEthMACCRCEngine((e+1)*8, self.width, self.polynom) for e in range(dw)]
        self.submodules += engines

        reg = Signal(self.width, reset=self.init)
        self.sync += reg.eq(engines[-1].crc_next)
        self.comb += [engines[e].data.eq(self.data[:(e+1)*8]) for e in range(dw)],
        self.comb += [engines[e].crc_prev.eq(reg) for e in range(dw)]
        self.comb += [If(last_be[e],
                        self.value.eq(reverse_bits(~engines[e].crc_next)),
                        self.error.eq(engines[e].crc_next != self.check))
                            for e in range(dw)]

# MAC CRC Inserter ---------------------------------------------------------------------------------

class LiteEthMACCRCInserter(LiteXModule):
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
    def __init__(self, crc_class, description):
        self.sink   = sink = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        # # #

        dw  = len(sink.data)
        assert dw in [8, 32, 64]
        crc = crc_class(dw)
        fsm = FSM(reset_state="IDLE")
        self.submodules += crc, fsm

        # crc packet checksum
        crc_packet = Signal(crc.width)
        last_be = Signal().like(sink.last_be)

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
            crc.data.eq(sink.data),
            crc.last_be.eq(sink.last_be),
            sink.connect(source),
            source.last.eq(0),
            source.last_be.eq(0),
            If(sink.last,
                # Fill the empty space of the last data word with the
                # beginning of the crc value
                [If(sink.last_be[e],
                    source.data.eq(Cat(sink.data[:(e+1)*8],
                        crc.value)[:dw])) for e in range(dw//8)],
                # If the whole crc value fits in the last sink paket, signal the
                # end. This also means the next state is idle
                If((dw == 64) & (sink.last_be <= 0xF),
                    source.last.eq(1),
                    source.last_be.eq(sink.last_be << (dw//8 - 4))
                ),
            ).Else(
                crc.ce.eq(sink.valid & source.ready),
            ),

            If(sink.valid & sink.last & source.ready,
                If((dw == 64) & (sink.last_be <= 0xF),
                    NextState("IDLE"),
                ).Else(
                    NextValue(crc_packet, crc.value),
                    If(dw == 64,
                        NextValue(last_be, sink.last_be >> 4),
                    ).Else (
                        NextValue(last_be, sink.last_be),
                    ),
                    NextState("CRC"),
                )
            )
        )
        ratio = crc.width//dw
        if ratio > 1:
            cnt = Signal(max=ratio, reset=ratio-1)
            cnt_done = Signal()
            fsm.act("CRC",
                source.valid.eq(1),
                chooser(crc_packet, cnt, source.data, reverse=True),
                If(cnt_done,
                    source.last.eq(1),
                    If(source.ready, NextState("IDLE"))
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
                    source.data.eq(crc_packet[-(e+1)*8:])) for e in range(dw//8)],
                If(source.ready, NextState("IDLE"))
            )


class LiteEthMACCRC32Inserter(LiteEthMACCRCInserter):
    def __init__(self, description):
        LiteEthMACCRCInserter.__init__(self, LiteEthMACCRC32, description)

# MAC CRC Checker ----------------------------------------------------------------------------------

class LiteEthMACCRCChecker(LiteXModule):
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
    def __init__(self, crc_class, description):
        self.sink   = sink   = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        self.error = Signal()

        # # #

        dw  = len(sink.data)
        assert dw in [8, 32, 64]
        crc = crc_class(dw)
        self.submodules += crc
        ratio = ceil(crc.width/dw)

        fifo = ResetInserter()(stream.SyncFIFO(description, ratio + 1))
        self.submodules += fifo

        fsm = FSM(reset_state="RESET")
        self.submodules += fsm

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

        fsm.act("RESET",
            crc.reset.eq(1),
            fifo.reset.eq(1),
            NextState("IDLE"),
        )
        self.comb += [
            crc.data.eq(sink.data),
            crc.last_be.eq(sink.last_be),
        ]
        fsm.act("IDLE",
            If(sink.valid & sink.ready,
                crc.ce.eq(1),
                NextState("COPY")
            )
        )
        last_be = Signal().like(sink.last_be)
        crc_error = Signal()
        fsm.act("COPY",
            fifo.source.ready.eq(fifo_out),
            source.valid.eq(sink.valid & fifo_full),
            source.payload.eq(fifo.source.payload),

            If(dw <= 32,
                source.last.eq(sink.last),
                source.last_be.eq(sink.last_be),
            # For dw == 64 bit, we need to look wether the last word contains only the crc value or both crc and data
            # In the latter case, the last word also needs to be output
            # In both cases, last_be needs to be adjusted for the new end position
            ).Elif(sink.last_be & 0xF,
                source.last.eq(sink.last),
                source.last_be.eq(sink.last_be << (dw//8 - 4)),
            ).Else(
                NextValue(last_be, sink.last_be >> 4),
                NextValue(crc_error, crc.error),
            ),

            # `source.error` has a width > 1 for dw > 8, but since the crc error
            # applies to the whole ethernet packet, all the bytes are marked as
            # containing an error. This way later reducing the data width
            # doesn't run into issues with missing the error
            source.error.eq(sink.error | Replicate(crc.error & sink.last, dw//8)),
            self.error.eq(sink.valid & sink.last & crc.error),

            If(sink.valid & sink.ready,
                crc.ce.eq(1),
                # Can only happen for dw == 64
                If(sink.last & (sink.last_be > 0xF),
                   NextState("COPY_LAST"),
                ).Elif(sink.last,
                    NextState("RESET")
                )
            )
        )

        # If the last sink word contains both data and the crc value, shift out
        # the last value here. Can only happen for dw == 64
        fsm.act("COPY_LAST",
            fifo.source.connect(source),
            source.error.eq(fifo.source.error | Replicate(crc_error, dw//8)),
            source.last_be.eq(last_be),
            If(source.valid & source.ready,
                NextState("RESET")
            )
        )


class LiteEthMACCRC32Checker(LiteEthMACCRCChecker):
    def __init__(self, description):
        LiteEthMACCRCChecker.__init__(self, LiteEthMACCRC32, description)
