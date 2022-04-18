#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2019 Vamsi K Vytla <vkvytla@lbl.gov>
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# SPDX-License-Identifier: BSD-2-Clause

from math import log2

from migen import *
from litex.gen import *

from litex.soc.interconnect.packet import Header, HeaderField
from litex.soc.interconnect import stream
# Packetizer ---------------------------------------------------------------------------------------

class Packetizer(Module):
    def __init__(self, sink_description, source_description, header):
        self.sink   = sink   = stream.Endpoint(sink_description)
        self.source = source = stream.Endpoint(source_description)
        self.header = Signal(header.length*8)

        # # #

        # Parameters.
        data_width      = len(self.sink.data)
        bytes_per_clk   = data_width//8
        header_words    = (header.length*8)//data_width
        header_leftover = header.length%bytes_per_clk
        aligned         = header_leftover == 0

        # Signals.
        sr       = Signal(header.length*8, reset_less=True)
        sr_load  = Signal()
        sr_shift = Signal()
        count    = Signal(max=max(header_words, 2))
        sink_d   = stream.Endpoint(sink_description)

        # Header Encode/Load/Shift.
        self.comb += header.encode(sink, self.header)
        self.sync += If(sr_load, sr.eq(self.header))
        if header_words != 1:
            self.sync += If(sr_shift, sr.eq(sr[data_width:]))

        source_last_a = Signal()
        source_last_b = Signal()
        source_last_s = Signal()

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm_from_idle = Signal()
        fsm.act("IDLE",
            sink.ready.eq(1),
            NextValue(count, 1),
            If(sink.valid,
                sink.ready.eq(0),
                source.valid.eq(1),
                source_last_a.eq(0),
                source.data.eq(self.header[:data_width]),
                If(source.valid & source.ready,
                    sr_load.eq(1),
                    NextValue(fsm_from_idle, 1),
                    If(header_words == 1,
                        NextState("ALIGNED-DATA-COPY" if aligned else "UNALIGNED-DATA-COPY")
                    ).Else(
                        NextState("HEADER-SEND")
                    )
               )
            )
        )
        fsm.act("HEADER-SEND",
            source.valid.eq(1),
            source_last_a.eq(0),
            source.data.eq(sr[min(data_width, len(sr)-1):]),
            If(source.valid & source.ready,
                sr_shift.eq(1),
                If(count == (header_words - 1),
                    sr_shift.eq(0),
                    NextState("ALIGNED-DATA-COPY" if aligned else "UNALIGNED-DATA-COPY"),
                    NextValue(count, count + 1)
               ).Else(
                    NextValue(count, count + 1),
               )
            )
        )
        fsm.act("ALIGNED-DATA-COPY",
            source.valid.eq(sink.valid),
            source_last_a.eq(sink.last),
            source.data.eq(sink.data),
            If(source.valid & source.ready,
               sink.ready.eq(1),
               If(source.last,
                  NextState("IDLE")
               )
            )
        )
        if not aligned:
            header_offset_multiplier = 1 if header_words == 1 else 2
            self.sync += If(source.valid & source.ready, sink_d.eq(sink))
            fsm.act("UNALIGNED-DATA-COPY",
                source.valid.eq(sink.valid | sink_d.last),
                source_last_a.eq(sink.last | sink_d.last),
                If(fsm_from_idle,
                    source.data[:max(header_leftover*8, 1)].eq(sr[min(header_offset_multiplier*data_width, len(sr)-1):])
                ).Else(
                    source.data[:max(header_leftover*8, 1)].eq(sink_d.data[min((bytes_per_clk-header_leftover)*8, data_width-1):])
                ),
                source.data[header_leftover*8:].eq(sink.data),
                If(source.valid & source.ready,
                    sink.ready.eq(~source.last | sink.last),
                    NextValue(fsm_from_idle, 0),
                    If(source.last,
                        NextState("IDLE")
                    )
                )
            )

        # Last BE.
        if hasattr(sink, "last_be") and hasattr(source, "last_be"):
            # For an 8-bit data path, last_be really should be 1 when last is
            # asserted, other values do not make sense. However, legacy code
            # might not set last_be at all, and thus it will be set to 0. To
            # remain compatible with this code, this "corrects" last_be for
            # 8-bit paths by setting it to the value of last.
            if len(sink.last_be) == 1:
                sink_last_be = Signal.like(sink.last_be)
                self.comb += [ sink_last_be.eq(sink.last) ]
            else:
                sink_last_be = sink.last_be

            # last_be needs to be right-rotated by the number of bytes which
            # would be required to have a properly aligned header.
            right_rot_by = header_leftover

            # Calculate a rotated last_be
            new_last_be = Signal.like(sink_last_be)
            self.comb += [
                new_last_be.eq(Cat([
                    sink_last_be[(i - right_rot_by) % bytes_per_clk]
                    for i in range(bytes_per_clk)
                ]))
            ]

            # Conditionally delay the calculated last_be for one clock cycle, if
            # it now applies to the next bus word OR if the source is not ready.
            delayed_last_be = Signal.like(sink_last_be)

            # FSM used to conveniently assign combinational and synchronous
            # signals in the same context.
            self.submodules.last_be_fsm = last_be_fsm = FSM(reset_state="DEFAULT")

            # Whether the main FSM is in one of the DATA-COPY states. This is
            # important as we overwrite sink.ready below and need to have
            # different behavior depending on the Packetizer's state
            in_data_copy = Signal()
            self.comb += [
                in_data_copy.eq(self.fsm.ongoing("ALIGNED-DATA-COPY") | self.fsm.ongoing("UNALIGNED-DATA-COPY"))
            ]

            self.last_be_fsm.act("DEFAULT",
                # Test whether our right-shift causes a wrap-around. In that
                # case apply the last value to the current bus word. Otherwise
                # delay it to the next.
                If(in_data_copy & sink.last & (sink_last_be > new_last_be),
                    # Right shift did not wrap around, need to delay the
                    # calculated last_be value and last by one cycle.
                    source_last_b.eq(0),
                    source_last_s.eq(1),
                    source.last_be.eq(0),
                    If(source.ready & source.valid,
                        NextValue(delayed_last_be, new_last_be),
                        NextState("DELAYED"),
                    ),
                ).Elif(in_data_copy,
                    # Output the calculated last_be value on the current packet
                    # already. For the next sink packet, ignore any last_be
                    source_last_b.eq(sink.last),
                    source_last_s.eq(1),
                    source.last_be.eq(new_last_be),
                ),
                If(in_data_copy,
                    sink.ready.eq(source.ready),
                ).Elif(self.fsm.ongoing("IDLE"),
                    sink.ready.eq(~sink.valid),
                )
            )

            self.last_be_fsm.act("DELAYED",
                # Output the delayed last and last_be signals
                source_last_b.eq(1),
                source_last_s.eq(1),
                source.last_be.eq(delayed_last_be),
                sink.ready.eq(0),
                If(source.ready,
                    NextState("DEFAULT"),
                ),
            )

        self.comb += [
            If(source_last_s,
                source.last.eq(source_last_b),
            ).Else(
                source.last.eq(source_last_a),
            )
        ]

        # Error.
        if hasattr(sink, "error") and hasattr(source, "error"):
            self.comb += source.error.eq(sink.error)

# Depacketizer -------------------------------------------------------------------------------------

class Depacketizer(Module):
    def __init__(self, sink_description, source_description, header):
        self.sink   = sink   = stream.Endpoint(sink_description)
        self.source = source = stream.Endpoint(source_description)
        self.header = Signal(header.length*8)

        # # #

        # Parameters.
        data_width      = len(sink.data)
        bytes_per_clk   = data_width//8
        header_words    = (header.length*8)//data_width
        header_leftover = header.length%bytes_per_clk
        aligned         = header_leftover == 0

        # Signals.
        sr                = Signal(header.length*8, reset_less=True)
        sr_shift          = Signal()
        sr_shift_leftover = Signal()
        count             = Signal(max=max(header_words, 2))
        sink_d            = stream.Endpoint(sink_description)

        # Header Shift/Decode.
        if (header_words) == 1 and (header_leftover == 0):
            self.sync += If(sr_shift, sr.eq(sink.data))
        else:
            self.sync += [
                If(sr_shift,          sr.eq(Cat(sr[bytes_per_clk*8:],   sink.data))),
                If(sr_shift_leftover, sr.eq(Cat(sr[header_leftover*8:], sink.data)))
            ]
        self.comb += self.header.eq(sr)
        self.comb += header.decode(self.header, source)

        source_last_a = Signal()
        source_last_b = Signal()
        source_last_s = Signal()

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm_from_idle = Signal()
        fsm.act("IDLE",
            sink.ready.eq(1),
            NextValue(count, 1),
            If(sink.valid,
                sr_shift.eq(1),
                NextValue(fsm_from_idle, 1),
                If(header_words == 1,
                    NextState("ALIGNED-DATA-COPY" if aligned else "UNALIGNED-DATA-COPY"),
                ).Else(
                    NextState("HEADER-RECEIVE")
                )
            )
        )
        fsm.act("HEADER-RECEIVE",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(count, count + 1),
                sr_shift.eq(1),
                If(count == (header_words - 1),
                    NextState("ALIGNED-DATA-COPY" if aligned else "UNALIGNED-DATA-COPY"),
                    NextValue(count, count + 1),
                )
            )
        )
        fsm.act("ALIGNED-DATA-COPY",
            source.valid.eq(sink.valid | sink_d.last),
            source_last_a.eq(sink.last | sink_d.last),
            sink.ready.eq(source.ready),
            source.data.eq(sink.data),
            If(source.valid & source.ready,
               If(source.last,
                  NextState("IDLE")
               )
            )
        )

        if not aligned:
            self.sync += If(sink.valid & sink.ready, sink_d.eq(sink))
            fsm.act("UNALIGNED-DATA-COPY",
                source.valid.eq(sink.valid | sink_d.last),
                source_last_a.eq(sink_d.last),
                sink.ready.eq(source.ready & ~source.last),
                source.data.eq(sink_d.data[header_leftover*8:]),
                source.data[min((bytes_per_clk-header_leftover)*8, data_width-1):].eq(sink.data),
                If(fsm_from_idle,
                    source.valid.eq(sink_d.last),
                    sink.ready.eq(~sink_d.last),
                    If(sink.valid,
                        NextValue(fsm_from_idle, 0),
                        sr_shift_leftover.eq(1),
                    )
                ),
                If(source.valid & source.ready,
                    If(source.last,
                        NextState("IDLE")
                    )
                )
            )

        # Error.
        if hasattr(sink, "error") and hasattr(source, "error"):
            self.comb += source.error.eq(sink.error)

        # Last BE.
        if hasattr(sink, "last_be") and hasattr(source, "last_be"):
            # For an 8-bit data path, last_be really should be 1 when last is
            # asserted, other values do not make sense. However, legacy code
            # might not set last_be at all, and thus it will be set to 0. To
            # remain compatible with this code, this "corrects" last_be for
            # 8-bit paths by setting it to the value of last.
            if len(sink.last_be) == 1:
                sink_last_be = Signal.like(sink.last_be)
                self.comb += [ sink_last_be.eq(sink.last) ]
            else:
                sink_last_be = sink.last_be

            # last_be needs to be left-rotated by the number of bytes which
            # would be required to have a properly aligned header.
            left_rot_by = (bytes_per_clk - header_leftover) % bytes_per_clk

            # Calculate a rotated last_be
            new_last_be = Signal.like(sink_last_be)
            self.comb += [
                new_last_be.eq(Cat([
                    sink_last_be[(i - left_rot_by) % bytes_per_clk]
                    for i in range(bytes_per_clk)
                ]))
            ]

            # Conditionally delay the calculated last_be for one clock cycle, if
            # it now applies to the next bus word.
            delayed_last_be = Signal.like(sink_last_be)

            # FSM used to conveniently assign combinational and synchronous
            # signals in the same context.
            self.submodules.last_be_fsm = last_be_fsm = FSM(reset_state="DEFAULT")

            # Whether the main FSM is / was in one of the DATA-COPY states. This
            # is important as we must handle a special case when last is
            # asserted while the Depacketizer has just transitioned out of
            # writing the header, which we can then detect by checking
            # (~was_in_copy & is_in_copy).
            is_in_copy = Signal()
            was_in_copy = Signal()
            self.comb += [
                is_in_copy.eq(
                    self.fsm.ongoing("ALIGNED-DATA-COPY") | self.fsm.ongoing("UNALIGNED-DATA-COPY")
                )
            ]
            self.sync += [
                was_in_copy.eq(is_in_copy)
            ]

            self.last_be_fsm.act("DEFAULT",
                # Test whether our left-shift has caused an wrap-around, in that
                # case delay last and last_be and apply to the next bus word.
                If(sink.valid & sink.last & (sink_last_be > new_last_be),
                    # last_be did wrap around. Need to delay the calculated
                    # last_be value and last by one cycle.
                    source_last_b.eq(0),
                    source_last_s.eq(1),
                    source.last_be.eq(0),
                    # Normally just wait until a source bus transaction occurred
                    # (ready + valid) until the delayed last_be can be
                    # output. However, if the first word is also the last, this
                    # will mean that the source isn't valid currently as the
                    # header is still being sent. Thus, if sink.valid and
                    # sink.last and we've just transitioned into the data copy
                    # phase, we can immediately jump into the DELAYED state (as
                    # the very first bus word containing proper data is also the
                    # last one, and we've just transitioned to putting that on
                    # the bus).
                    If((source.ready & source.valid) | (~was_in_copy & is_in_copy),
                        NextValue(delayed_last_be, new_last_be),
                        NextState("DELAYED"),
                    ),
                ).Elif(sink.last,
                    # Simply forward the calculated last_be value.
                    source_last_b.eq(1),
                    source_last_s.eq(1),
                    source.last_be.eq(new_last_be),
                ),
                If(self.fsm.ongoing("ALIGNED-DATA-COPY") \
                   | (self.fsm.ongoing("UNALIGNED-DATA-COPY") & ~fsm_from_idle),
                    sink.ready.eq(source.ready),
                ).Else(
                    sink.ready.eq(1),
                ),
            )

            self.last_be_fsm.act("DELAYED",
                # Output the delayed last and last_be signals
                source_last_b.eq(1),
                source_last_s.eq(1),
                source.last_be.eq(delayed_last_be),
                sink.ready.eq(0),
                If(source.ready & source.valid,
                    NextState("DEFAULT"),
                ),
            )

        self.comb += [
            If(source_last_s,
                source.last.eq(source_last_b),
            ).Else(
                source.last.eq(source_last_a),
            ),
        ]
