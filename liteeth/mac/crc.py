# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# This file is Copyright (c) 2017 whitequark <whitequark@whitequark.org>
# This file is Copyright (c) 2018 Felix Held <felix-github@felixheld.de>
# License: BSD

from functools import reduce
from operator import xor
from collections import OrderedDict

from liteeth.common import *

from migen.genlib.misc import chooser, WaitTimer


class LiteEthMACCRCEngine(Module):
    """Cyclic Redundancy Check Engine

    Compute next CRC value from last CRC value and data input using
    an optimized asynchronous LFSR.

    Parameters
    ----------
    data_width : int
        Width of the data bus.
    width : int
        Width of the CRC.
    polynom : int
        Polynom of the CRC (ex: 0x04C11DB7 for IEEE 802.3 CRC)

    Attributes
    ----------
    data : in
        Data input.
    last : in
        last CRC value.
    next :
        next CRC value.
    """
    def __init__(self, data_width, width, polynom):
        self.data = Signal(data_width)
        self.last = Signal(width)
        self.next = Signal(width)

        # # #

        def _optimize_eq(l):
            """
            remove an even numbers of XORs with the same bit
            replace an odd number of XORs with a single XOR
            """
            d = OrderedDict()
            for e in l:
                if e in d:
                    d[e] += 1
                else:
                    d[e] = 1
            r = []
            for key, value in d.items():
                if value%2 != 0:
                    r.append(key)
            return r

        # compute and optimize the parallel implementation of the CRC's LFSR
        taps = [x for x in range(width) if (1 << x) & polynom]
        curval = [[("state", i)] for i in range(width)]
        for i in range(data_width):
            feedback = curval.pop() + [("din", i)]
            for j in range(width-1):
                if j+1 in taps:
                    curval[j] += feedback
                curval[j] = _optimize_eq(curval[j])
            curval.insert(0, feedback)

        # implement logic
        for i in range(width):
            xors = []
            for t, n in curval[i]:
                if t == "state":
                    xors += [self.last[n]]
                elif t == "din":
                    xors += [self.data[n]]
            self.comb += self.next[i].eq(reduce(xor, xors))


@ResetInserter()
@CEInserter()
class LiteEthMACCRC32(Module):
    """IEEE 802.3 CRC

    Implement an IEEE 802.3 CRC generator/checker.

    Parameters
    ----------
    data_width : int
        Width of the data bus.

    Attributes
    ----------
    d : in
        Data input.
    value : out
        CRC value (used for generator).
    error : out
        CRC error (used for checker).
    """
    width = 32
    polynom = 0x04C11DB7
    init = 2**width-1
    check = 0xC704DD7B
    def __init__(self, data_width):
        self.data = Signal(data_width)
        self.value = Signal(self.width)
        self.error = Signal()

        # # #

        self.submodules.engine = LiteEthMACCRCEngine(data_width, self.width, self.polynom)
        reg = Signal(self.width, reset=self.init)
        self.sync += reg.eq(self.engine.next)
        self.comb += [
            self.engine.data.eq(self.data),
            self.engine.last.eq(reg),

            self.value.eq(~reg[::-1]),
            self.error.eq(self.engine.next != self.check)
        ]


@ResetInserter()
@CEInserter()
class LiteEthMACCRC32_64(Module):
    """IEEE 802.3 CRC

    Implement an IEEE 802.3 CRC generator/checker.

    Parameters
    ----------
    data_width : int
        Width of the data bus.

    Attributes
    ----------
    d : in
        Data input.
    value : out
        CRC value (used for generator).
    error : out
        CRC error (used for checker).
    """
    width = 32
    polynom = 0x04C11DB7
    init = 2**width-1
    check = 0xC704DD7B
    def __init__(self, data_width):
        self.data = Signal(data_width)
        self.last = Signal()
        self.value = Signal(self.width)
        self.error_i = Signal(5)
        self.error = Signal()
        self.last_be = Signal(8)
        # # #

        self.submodules.engine4 = LiteEthMACCRCEngine(data_width, self.width, self.polynom)
        self.submodules.engine3 = LiteEthMACCRCEngine(data_width//2, self.width, self.polynom)
        self.submodules.engine2 = LiteEthMACCRCEngine(24, self.width, self.polynom)
        self.submodules.engine1 = LiteEthMACCRCEngine(data_width//4, self.width, self.polynom)
        self.submodules.engine0 = LiteEthMACCRCEngine(data_width//8, self.width, self.polynom)

        reg = Signal(self.width, reset=self.init)
        engine_data_in = Signal(data_width)
        second_nibble, second_nibble_d = Signal(), Signal()
        last_d = Signal()
        last_be_d = Signal(len(self.last_be))
        self.sync += [
            last_d.eq(self.last),
            last_be_d.eq(self.last_be),
            second_nibble_d.eq(second_nibble),
            self.engine4.data.eq(self.data),
            If(self.last_be > 8,
               reg.eq(self.engine3.next),
               self.engine3.data.eq(self.data[33:]),
               self.engine2.data.eq(self.data[33:57]),
               self.engine1.data.eq(self.data[33:49]),
               self.engine0.data.eq(self.data[33:41]),
               second_nibble.eq(1),
            ).Else(reg.eq(self.engine4.next),
                   self.engine3.data.eq(self.data[0:32]),
                   self.engine2.data.eq(self.data[0:24]),
                   self.engine1.data.eq(self.data[0:16]),
                   self.engine0.data.eq(self.data[0:8]),
                   second_nibble.eq(0))
            ]
        self.comb += [

            self.engine4.last.eq(reg),
            self.engine3.last.eq(reg),
            self.engine2.last.eq(reg),
            self.engine1.last.eq(reg),
            self.engine0.last.eq(reg),
            self.error_i[4].eq(self.engine4.next != self.check),
            self.error_i[3].eq(self.engine3.next != self.check),
            self.error_i[2].eq(self.engine2.next != self.check),
            self.error_i[1].eq(self.engine1.next != self.check),
            self.error_i[0].eq(self.engine0.next != self.check),

            self.error.eq((~second_nibble & self.last & self.error_i[0] & (self.last_be == 0x00)) |
                          (~second_nibble & self.last & self.error_i[1] & (self.last_be == 0x01)) |
                          (~second_nibble & self.last & self.error_i[2] & (self.last_be == 0x02)) |
                          (~second_nibble & self.last & self.error_i[3] & (self.last_be == 0x04)) |
                          ( second_nibble & self.last & self.error_i[4] & (self.last_be == 0x80)) |
                          ( second_nibble_d & last_d & self.error_i[0] & (last_be_d == 0x10)) |
                          ( second_nibble_d & last_d & self.error_i[1] & (last_be_d == 0x20)) |
                          ( second_nibble_d & last_d & self.error_i[2] & (last_be_d == 0x40))),

        ]


class LiteEthMACCRCInserter(Module):
    """CRC Inserter

    Append a CRC at the end of each packet.

    Parameters
    ----------
    description : description
        description of the dataflow.

    Attributes
    ----------
    sink : in
        Packets octets without CRC.
    source : out
        Packets octets with CRC.
    """
    def __init__(self, crc_class, description):
        self.sink = sink = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        # # #

        dw = len(sink.data)
        crc = crc_class(dw)
        fsm = FSM(reset_state="IDLE")
        self.submodules += crc, fsm

        cw = dw//8
        rotate_by = 4 % cw
        last_be = Signal(cw)
        x = [sink.last_be[(i + rotate_by) % cw] for i in range(cw)]

        self.sync += If(sink.last, last_be.eq(Cat(*x)))

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
            sink.connect(source),
            source.last.eq(0),
            If(sink.valid & sink.last & source.ready,
                NextState("INSERT"),
            )
        )
        ratio = crc.width//dw
        if ratio > 1:
            cnt = Signal(max=ratio, reset=ratio-1)
            cnt_done = Signal()
            fsm.act("INSERT",
                source.valid.eq(1),
                chooser(crc.value, cnt, source.data, reverse=True),
                If(cnt_done,
                    source.last.eq(1),
                    If(source.ready, NextState("IDLE"))
                )
            )
            self.comb += cnt_done.eq(cnt == 0)
            self.sync += \
                If(fsm.ongoing("IDLE"),
                    cnt.eq(cnt.reset)
                ).Elif(fsm.ongoing("INSERT") & ~cnt_done,
                    cnt.eq(cnt - source.ready)
                )
        else:
            fsm.act("INSERT",
                source.valid.eq(1),
                source.last.eq(1),
                source.last_be.eq(1 << (4 % (dw//8) - 1)),
                source.data.eq(crc.value),
                If(source.ready, NextState("IDLE"))
            )


class LiteEthMACCRC32Inserter(LiteEthMACCRCInserter):
    def __init__(self, description):
        LiteEthMACCRCInserter.__init__(self, LiteEthMACCRC32, description)


class LiteEthMACCRCChecker(Module):
    """CRC Checker

    Check CRC at the end of each packet.

    Parameters
    ----------
    description : description
        description of the dataflow.

    Attributes
    ----------
    sink : in
        Packet octets with CRC.
    source : out
        Packet octets without CRC and "error" set to 0
        on last when CRC OK / set to 1 when CRC KO.
    error : out
        Pulses every time a CRC error is detected.
    """
    def __init__(self, crc_class, description):
        self.sink = sink = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)
        self.comb += sink.connect(source)

        self.error = Signal()

        # # #

        dw = len(sink.data)

        if dw == 64:
            crc = LiteEthMACCRC32_64(dw)
            self.submodules += crc
            ratio = crc.width//dw + 1  # Keep a FIFO of 1
            fifo = ResetInserter()(stream.SyncFIFO(description, ratio + 1))
            self.submodules += fifo

            fsm = FSM(reset_state="RESET")
            self.submodules += fsm

            fifo_in = Signal()
            fifo_out = Signal()
            fifo_full = Signal()

            self.comb += [
                fifo_full.eq(fifo.level == ratio),
                fifo_in.eq(sink.valid & (~fifo_full | fifo_out)),
                fifo_out.eq(source.valid & source.ready),

                sink.connect(fifo.sink),
                fifo.sink.valid.eq(fifo_in),
                self.sink.ready.eq(fifo_in),

                source.valid.eq(sink.valid & fifo_full),
                source.last.eq(sink.last),
                fifo.source.ready.eq(fifo_out),
                source.payload.eq(fifo.source.payload),
                source.last_be.eq(sink.last_be),
                source.error.eq(sink.error | crc.error),
                self.error.eq(source.valid & source.last & crc.error),

            ]

            fsm.act("RESET",
                crc.reset.eq(1),
                fifo.reset.eq(1),
                NextState("IDLE"),
            )
            self.comb += [crc.data.eq(sink.data),
                          crc.last.eq(sink.last),
                          crc.last_be.eq(sink.last_be)
            ]
            fsm.act("IDLE",
                If(sink.valid & sink.ready,
                    crc.ce.eq(1),
                    NextState("COPY")
                )
            )
            fsm.act("COPY",
                If(sink.valid & sink.ready,
                   crc.ce.eq(1),
                   If(sink.last,
                      NextState("RESET")
                    )
                )
            )
        else:
            crc = crc_class(dw)
            self.submodules += crc
            ratio = crc.width//dw
            fifo = ResetInserter()(stream.SyncFIFO(description, ratio + 1))
            self.submodules += fifo

            fsm = FSM(reset_state="RESET")
            self.submodules += fsm

            fifo_in = Signal()
            fifo_out = Signal()
            fifo_full = Signal()

            self.comb += [
                fifo_full.eq(fifo.level == ratio),
                fifo_in.eq(sink.valid & (~fifo_full | fifo_out)),
                fifo_out.eq(source.valid & source.ready),

                sink.connect(fifo.sink),
                fifo.sink.valid.eq(fifo_in),
                self.sink.ready.eq(fifo_in),

                source.valid.eq(sink.valid & fifo_full),
                source.last.eq(sink.last),
                fifo.source.ready.eq(fifo_out),
                source.payload.eq(fifo.source.payload),

                source.error.eq(sink.error | crc.error),
                self.error.eq(source.valid & source.last & crc.error),
            ]

            fsm.act("RESET",
                crc.reset.eq(1),
                fifo.reset.eq(1),
                NextState("IDLE"),
            )
            self.comb += crc.data.eq(sink.data)
            fsm.act("IDLE",
                If(sink.valid & sink.ready,
                    crc.ce.eq(1),
                    NextState("COPY")
                )
            )
            fsm.act("COPY",
                If(sink.valid & sink.ready,
                    crc.ce.eq(1),
                    If(sink.last,
                        NextState("RESET")
                    )
                )
            )


class LiteEthMACCRC32Checker(LiteEthMACCRCChecker):
    def __init__(self, description):
        LiteEthMACCRCChecker.__init__(self, LiteEthMACCRC32, description)
