from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import Endpoint, Buffer, SyncFIFO

class VarWaitTimer(Module):
    """Variable Wait Timer

    A timer whose wait time can be changed to a value in {4.096*2^i, i in [[0, 31]]} us
    These times come from the specification

    See section 12.7.5: LOCAL CM RESPONSE TIMEOUT

    Parameters
    ----------
    clk_freq : int
        Clock frequency.


    Attributes
    ----------
    wait : in
        Launch and wait for timer.
    pow : in
        The timer waits for 4.096*2^pow us
    done : out
        Time is out.
    """
    def __init__(self, clk_freq):
        self.wait = Signal()
        self.pow = Signal(5)
        self.done = Signal()

        # # #

        bits = log2_int(
            n = ceil(4.096e-6*(2**((1 << len(self.pow)) - 1))*clk_freq) + 1,
            need_pow2 = False
        )
        # Cast t to int.
        cnt_dict = {
            Constant(i, len(self.pow)):
                Constant(ceil(4.096e-6*(2**i)*clk_freq), bits)
            for i in range(1 << len(self.pow))
        }
        rst_count = Signal(bits)
        count = Signal(reset_less=True).like(rst_count)

        self.comb += Case(self.pow, {i: rst_count.eq(v) for i, v in cnt_dict.items()})
        self.comb += self.done.eq(count == 0)
        self.sync += [
            If(self.wait,
                If(~self.done,
                    count.eq(count - 1)
                )
            ).Else(
                count.eq(rst_count)
            )
        ]

class WaitPipe(LiteXModule):
    """Wait Pipe

    A block FIFO for packet payloads whose latest packet can be invalidated.

    Parameters
    ----------
    layout : description
        Description of the dataflow.
    depth : int
        Depth of the underlying FIFO.
    block_size : int
        Size of one packet payload (PMTU)
    discarding : bool
        When the fifo is full, if the WaitPipe is
        discarding, it will clear the last incoming
        packet instead of making sink wait by setting
        ready to 0.
    buffered_in : bool
        Whether to buffer input to cut timing
    buffered_out : bool
        Whether to buffer output to cut timing
    dw : int
        Width of the data bus.


    Attributes
    ----------
    sink : in
        Incoming packet data.
    validate_sink : in
        sink for packet validation
    header_only : in
        Makes WaitPipe consider the output packet as
        empty (only presents parameters and last until
        ready is asserted)
    source : out
        Outgoing packet data
    full: out
        Indicates if the pipe is full
    """
    def __init__(self,
                 layout,
                 depth,
                 block_size,
                 discarding=True,
                 buffered_in=True,
                 buffered_out=True,
                 dw=8
        ):
        # Header only packets are treated slightly differently
        layout = add_params(layout, [("header_only", 1)])
        # invalidate
        #    Invalidates current packet and gets ready for
        #    the next one
        # validate
        #    Validates the last full packet and gets ready
        #    for the next one
        validate_layout = [
            ("invalidate", 1, DIR_M_TO_S),
            ("validate",   1, DIR_M_TO_S)
        ]
        # In
        self.sink          = sink          = Endpoint(layout)
        self.validate_sink = validate_sink = Record(validate_layout)

        # Out
        self.source = source = Endpoint(layout)
        self.full   = full   = Signal()

        # # #

        # Check if block_size is power of 2
        log2_int(block_size, need_pow2=True)

        # Saved parameters
        fifo_layout = layout.param_layout + [("last_addr", log2_int(block_size))]
        self.param_fifo = param_fifo = SyncFIFO(fifo_layout, depth)

        if buffered_in:
            buff_in = Buffer(layout, pipe_ready=True)
            self.submodules.buff_in = buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

            buff_in_validate = Record(validate_layout)
            self.sync += validate_sink.connect(buff_in_validate)
            validate_sink = buff_in_validate

        if buffered_out:
            buff_out = Buffer(layout, pipe_ready=True)
            self.submodules.buff_out = buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink

        # Internal ram
        array = Memory(dw, block_size * depth, name="wait_pipe_mem")
        in_port  = array.get_port(write_capable=True)
        out_port = array.get_port()
        self.specials += array, in_port, out_port

        # Dump packet in in buffer
        invalidate = Signal()
        # Transfer packet from in to out buffer
        validate    = Signal()
        # Stays on after validate set and until out buffer is freed
        validated = Signal()

        self.comb += [
            validate.eq(validate_sink.validate),
            invalidate.eq(validate_sink.invalidate)
        ]

        # Pipe level logic
        level = Signal(max=depth+1)
        inc   = Signal()
        dec   = Signal()

        # Writing and reading position tracking
        # Position inside block
        local_in_addr  = Signal(max=block_size)
        local_out_addr = Signal(max=block_size)

        # Block position
        in_block  = Signal(max=depth)
        out_block = Signal(max=depth)

        # Combined positions
        real_in_addr  = Signal(max=depth*block_size)
        real_out_addr = Signal(max=depth*block_size)

        # Additionnal reading logic
        reading_addr    = Signal().like(local_out_addr)
        local_nout_addr = Signal().like(local_out_addr)

        saved_last_addr = Signal(log2_int(block_size), reset_less=True)
        choose_comb_last_addr = Signal()

        self.comb += [
            If(validate,
                # Write parameters to memory Record
                [
                    param_fifo.sink.__getattr__(sig[0]).eq(sink.__getattr__(sig[0]))
                    for sig in layout.param_layout
                ],
                If(choose_comb_last_addr,
                    param_fifo.sink.last_addr.eq(local_in_addr),
                ).Else(
                    param_fifo.sink.last_addr.eq(saved_last_addr)
                ),
                param_fifo.sink.valid.eq(1)
            )
        ]

        self.sync += [
            If(inc & ~dec,
                level.eq(level + 1)
            ).Elif(~inc & dec,
                level.eq(level - 1)
            )
        ]

        self.comb += [
            real_in_addr.eq(Cat(local_in_addr, in_block)),
            real_out_addr.eq(Cat(reading_addr, out_block))
        ]

        # Output signals logic
        self.comb += [
            [
                source.__getattr__(sig[0]).eq(param_fifo.source.__getattr__(sig[0]))
                for sig in layout.param_layout
            ],
            # Adding & ~dec would also work, but it would link output and input combinatorially
            full.eq(level == ((depth - 1) if discarding else (depth)))
        ]

        # In buffer FSM logic
        self.comb += [
            in_port.adr.eq(real_in_addr),
            in_port.dat_w.eq(sink.data)
        ]

        self.in_fsm = in_fsm = FSM(reset_state="WRITE")
        in_fsm.act("WRITE",
            sink.ready.eq(1),
            If(sink.valid,
                in_port.we.eq(1),
                NextValue(local_in_addr, local_in_addr + 1),
                If(invalidate,
                    NextValue(local_in_addr, 0),
                ).Elif(sink.last,
                    NextValue(saved_last_addr, local_in_addr),
                    choose_comb_last_addr.eq(1),
                    NextState("WAIT"),
                )
            )
        )

        self.sync += [
            If(in_fsm.ongoing("WAIT"),
                If(validated,
                    validated.eq(0)
                )
            ).Else(
                If(validate,
                    validated.eq(1)
                )
            )
        ]

        if discarding:
            in_fsm.act("WAIT",
                NextValue(local_in_addr, 0),
                If(validated | validate,
                    If((level == depth - 1) & ~dec,
                        NextState("WRITE")
                    ).Else(
                        If(in_block == depth - 1,
                            NextValue(in_block, 0)
                        ).Else(
                            NextValue(in_block, in_block + 1)
                        ),
                        inc.eq(1),
                        NextState("WRITE")
                    )
                ).Elif(invalidate,
                    NextState("WRITE")
                )
            )

        else:
            in_fsm.act("WAIT",
                NextValue(local_in_addr, 0),
                If(validated | validate,
                    If(in_block == depth - 1,
                        NextValue(in_block, 0)
                    ).Else(
                        NextValue(in_block, in_block + 1)
                    ),
                    inc.eq(1),
                    If((level == depth - 1) & ~dec,
                        NextState("FULL")
                    ).Else(
                        NextState("WRITE")
                    )
                ).Elif(invalidate,
                    NextState("WRITE")
                )
            )

            in_fsm.act("FULL",
                If(dec,
                    NextState("WRITE")
                )
            )

        # Out buffer FSM logic
        self.out_fsm = out_fsm = FSM(reset_state="WAIT")
        out_fsm.act("WAIT",
            If(level != 0 | inc,
                NextState("READ")
            )
        )

        # Reading address logic
        self.comb += [
            local_nout_addr.eq(local_out_addr + 1),
            If(out_fsm.ongoing("READ"),
                If(source.ready,
                    reading_addr.eq(local_nout_addr),
                ).Else(
                    reading_addr.eq(local_out_addr)
                )
            ).Else(
                reading_addr.eq(local_out_addr)
            ),

            out_port.adr.eq(real_out_addr),
            source.data.eq(out_port.dat_r)
        ]
        out_fsm.act("READ",
            source.valid.eq(1),
            source.last.eq((local_out_addr == param_fifo.source.last_addr) | source.header_only),
            If(source.ready,
                NextValue(local_out_addr, local_nout_addr),
                If(source.last,
                    param_fifo.source.ready.eq(1),
                    If(out_block == depth-1,
                        NextValue(out_block, 0)
                    ).Else(
                        NextValue(out_block, out_block + 1)
                    ),
                    dec.eq(1),
                    NextValue(local_out_addr, 0),
                    NextState("WAIT")
                )
            )
        )

# Variable Packetizer ---------------------------------------------------------------------------------------

class VariablePacketizer(LiteXModule):
    """Variable Packetizer

    A packetizer that can handle headers that change depending on an opcode.

    Parameters
    ----------
    sink_description : description
        Headers + data.
    source_description : description
        Raw data.
    headers : list[Header]
        Possible headers in the packet
        (including an always-present first header
        that must contain the opcode).
    opmap: dict[int, int]
        Indicates which headers are selected depending on the opcode.
    opcode_name : str
        Name of the opcode signal.


    Attributes
    ----------
    sink : in
        Incoming packet data and parameters.
    source : out
        Outgoing packet data.

    """
    def __init__(self, sink_description, source_description, headers, opmap, opcode_name="opcode"):
        self.sink   = sink   = Endpoint(add_params(sink_description, [("header_only", 1)]))
        self.source = source = Endpoint(source_description)

        # # #

        assert hasattr(sink, opcode_name)

        # Parameters.
        data_width = len(sink.data)
        base_header       = headers[0]
        base_header_words = (base_header.length * 8) // data_width
        header_words_dict = {
            opcode:
                base_header_words +
                (sum(
                    header.length
                    for i, header in list(enumerate(headers))[1:]
                    if (selection >> (i - 1)) & 1
                )*8) // data_width
            for opcode, selection in opmap.items()
        }
        max_size = max(header_words_dict.values())

        # Signals.
        header_length = Signal(bits_for(max_size))
        header = Signal(max_size*8)

        sr       = Signal(max(header_words_dict.values())*8, reset_less=True)
        sr_load  = Signal()
        sr_shift = Signal()
        count    = Signal(max=max(*header_words_dict.values(), 2))
        header_words = Signal(max=max(header_words_dict.values())+1)

        # Determine header_words
        self.comb += Case(sink.__getattr__(opcode_name), {
            Constant(opcode, len(sink.__getattr__(opcode_name))):
                header_words.eq(Constant(length, len(header_words))) for opcode, length in header_words_dict.items()
        })
        self.comb += header_length.eq(base_header_words + header_words)

        # Header Encode/Load/Shift.
        cases = {}
        for opcode, selection in opmap.items():
            cum = base_header_words
            assignments = []
            for i, extra_header in list(enumerate(headers))[1:]:
                if (selection >> (i - 1)) & 1:
                    assignments += extra_header.encode(sink, header, cum)
                    cum += extra_header.length
            cases[opcode] = assignments

        self.comb += base_header.encode(sink, header)
        self.comb += Case(sink.__getattr__(opcode_name), cases)
        self.sync += If(sr_load, sr.eq(header))
        self.sync += If(header_words != 1, If(sr_shift, sr.eq(sr[data_width:])))

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(0),
            NextValue(count, 1),
            If(sink.valid,
                source.valid.eq(1),
                source.last.eq((header_words == 1) & sink.header_only),
                source.data.eq(header[:data_width]),
                If(source.valid & source.ready,
                    sr_load.eq(1),
                    If(header_words == 1,
                        NextState("ALIGNED-DATA-COPY"),
                        If(sink.header_only,
                           NextState("IDLE")
                        )
                    ).Else(
                        NextState("HEADER-SEND")
                    )
               )
            )
        )
        fsm.act("HEADER-SEND",
            source.valid.eq(1),
            source.last.eq((count == header_words - 1) & sink.header_only),
            source.data.eq(sr[min(data_width, len(sr)-1):]),
            If(source.valid & source.ready,
                sr_shift.eq(1),
                If(count == header_words - 1,
                    sr_shift.eq(0),
                    NextState("ALIGNED-DATA-COPY"),
                    NextValue(count, count + 1),
                    If(sink.header_only,
                        sink.ready.eq(1),
                        NextState("IDLE")
                    )
                ).Else(
                    NextValue(count, count + 1),
                )
            )
        )
        fsm.act("ALIGNED-DATA-COPY",
            sink.connect(source, keep={"valid", "ready", "last", "data"}),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE")
                )
            )
        )

        # Error.
        if hasattr(sink, "error") and hasattr(source, "error"):
            self.comb += source.error.eq(sink.error)

# Variable Depacketizer -------------------------------------------------------------------------------------

class VariableDepacketizer(LiteXModule):
    """Variable Depacketizer

    A depacketizer that can handle headers that change depending on an opcode in the packet.

    Parameters
    ----------
    sink_description : description
        Raw data.
    source_description : description
        Headers + data.
    headers : list[Header]
        Possible headers in the packet
        (including an always-present first header
        that must contain the opcode).
    opmap: dict[int, int]
        Indicates which headers are selected depending on the opcode.
    opcode_name : str
        Name of the opcode signal.


    Attributes
    ----------
    sink : in
        Incoming packet data.
    source : out
        Outgoing packet data and parameters.

    """
    def __init__(self, sink_description, source_description, headers, opmap, opcode_name="opcode"):
        self.sink   = sink   = Endpoint(sink_description)
        self.source = source = Endpoint(add_params(source_description, [("header_only", 1)]))

        # # #

        assert hasattr(source, opcode_name)

        # Parameters.
        data_width        = len(sink.data)
        bytes_per_clk     = data_width//8
        base_header       = headers[0]
        base_header_words = (base_header.length * 8) // data_width
        # Does not include base header
        header_words_dict = {
            opcode:
                (sum(
                    header.length
                    for i, header in list(enumerate(headers))[1:]
                    if (selection >> (i - 1)) & 1
                )*8) // data_width
            for opcode, selection in opmap.items()
        }
        max_header_words = max(header_words_dict.values())

        # Signals.

        sr            = Signal(max_header_words*8, reset_less=True)
        sr_shift      = Signal()
        bhsr          = Signal(base_header_words*8, reset_less=True)
        bhsr_shift    = Signal()
        count         = Signal(max=max(base_header_words + max_header_words, 2))
        header_words  = Signal(max=max_header_words + 1)
        header_length = Signal(max=base_header_words + max_header_words + 1)

        # Determine header_words

        self.comb += Case(source.__getattr__(opcode_name), {
            Constant(opcode, len(source.__getattr__(opcode_name))):
                header_words.eq(Constant(length, len(header_words))) for opcode, length in header_words_dict.items()
        })
        self.comb += header_length.eq(base_header_words + header_words)

        # Header Shift/Decode.
        self.sync += [
            If(header_words == 1,
                If(sr_shift, sr.eq(sink.data))
            ).Else(
                If(sr_shift, sr.eq(Cat(sr[bytes_per_clk*8:], sink.data)))
            )
        ]
        if base_header_words == 1:
            self.sync += If(bhsr_shift, bhsr.eq(sink.data))
        else:
            self.sync += If(bhsr_shift, bhsr.eq(Cat(bhsr[bytes_per_clk*8:], sink.data)))

        # Select decodes depending on opcode
        decode_cases = {}
        for opcode, selection in opmap.items():
            # bits need to be shifted to end of register
            shift = (max_header_words - header_words_dict[opcode])*8
            cum = 0
            decode_cases[opcode] = []
            for i, header in list(enumerate(headers))[1:]:
                if (selection >> (i - 1)) & 1:
                    # Decode the appropriate header selection into the
                    decode_cases[opcode] += header.decode(sr[shift:], source, cum)
                    cum += header.length

        self.comb += Case(source.__getattr__(opcode_name), decode_cases)
        self.comb += base_header.decode(bhsr, source)

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            NextValue(count, 1),
            If(sink.valid,
                bhsr_shift.eq(1),
                If(base_header_words == 1,
                    NextState("DECODE-OPCODE"),
                    If(sink.last & sink.valid,
                        NextState("HEADER-ONLY")
                    )
                ).Else(
                    NextState("BASE-HEADER-RECEIVE"),
                )
            )
        )

        fsm.act("BASE-HEADER-RECEIVE",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(count, count + 1),
                bhsr_shift.eq(1),
                If(count == (base_header_words - 1),
                    NextState("DECODE-OPCODE"),
                    If(sink.last & sink.valid,
                        NextState("HEADER-ONLY")
                    )
                )
            )
        )

        fsm.act("DECODE-OPCODE",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(count, count + 1),
                If(header_words == 0,
                    source.valid.eq(sink.valid),
                    source.last.eq(sink.last),
                    sink.ready.eq(source.ready),
                    source.data.eq(sink.data),
                    NextState("ALIGNED-DATA-COPY"),
                ).Elif(header_words == 1,
                    sr_shift.eq(1),
                    NextState("ALIGNED-DATA-COPY"),
                    If(sink.last & sink.valid,
                        NextState("HEADER-ONLY")
                    )
                ).Else(
                    sr_shift.eq(1),
                    NextValue(count, count + 1),
                    NextState("HEADER-RECEIVE")
                )
            )
        )

        fsm.act("HEADER-RECEIVE",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(count, count + 1),
                sr_shift.eq(1),
                If(count == (base_header_words + header_words - 1),
                    NextState("ALIGNED-DATA-COPY"),
                    NextValue(count, count + 1),
                    If(sink.last & sink.valid,
                        NextState("HEADER-ONLY")
                    )
                )
            )
        )

        fsm.act("ALIGNED-DATA-COPY",
            source.valid.eq(sink.valid),
            source.last.eq(sink.last),
            sink.ready.eq(source.ready),
            source.data.eq(sink.data),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE")
                )
            )
        )

        fsm.act("HEADER-ONLY",
            source.header_only.eq(1),
            source.valid.eq(1),
            source.last.eq(1),
            If(source.ready,
                NextState("IDLE")
            )
        )

        # Error.
        if hasattr(sink, "error") and hasattr(source, "error"):
            self.comb += source.error.eq(sink.error)
