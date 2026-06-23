from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import Endpoint

from litex.gen.genlib.misc import chooser

# ICRC ---------------------------------------------------------------------------------------------
@ResetInserter()
@CEInserter()
class LiteEthInfinibandCRC32(LiteXModule):
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
    # 8 0xff bytes are inserted into crc to simulate a dummy LRH (which isn't present in RoCEv2)
    init    = 0xc704dd7b
    xor_final = 0xFFFFFFFF
    check   = 0xc704dd7b
    def __init__(self, data_width):
        self.data  = Signal(data_width)
        self.be    = Signal(data_width//8, reset=2**data_width//8 - 1)
        self.value = Signal(self.width)
        self.error = Signal()

        # # #

        from liteeth.mac.crc import LiteEthMACCRCEngine
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
                    self.value.eq(engines[n].crc_next[::-1] ^ self.xor_final),
                    self.error.eq(engines[n].crc_next != self.check),
                )
            ]

@ResetInserter()
class LiteEthInfinibandICRCCalculator(LiteXModule):
    """CRC Calculator

    Calculates CRC masking the correct IPv4 and UDP fields for RoCEv2.

    Parameters
    ----------
    description : description
        description of the dataflow.

    Attributes
    ----------
    ce : in
        Activates calculator for this cycle.
    data : in
        Packet data without CRC.
    crc_packet : out
        Resulting CRC value.
    """
    def __init__(self, dw):
        self.ce         = ce         = Signal()
        self.data       = data       = Signal(dw)
        self.crc_packet = crc_packet = Signal(32, reset_less=True)

        # # #

        # Parameters.
        assert dw in [8]

        # Signals.
        byte_cnt = Signal(16)

        # CRC32 Generator.
        crc = LiteEthInfinibandCRC32(dw)
        self.submodules += crc

        self.comb += [
            # Variable bytes are masked in ICRC
            If(is_in(byte_cnt, [1, 8, 10, 11, 26, 27, 32]),
                crc.data.eq(0xff)
            ).Else(
                crc.data.eq(data)
            ),
            crc.ce.eq(ce),
        ]

        self.sync += [
            If(ce,
                byte_cnt.eq(byte_cnt + 1),
                crc_packet.eq(crc.value)
            ),
        ]

class LiteEthInfinibandICRCInserter(LiteXModule):
    """CRC Inserter

    Append a CRC at the end of each packet.

    Parameters
    ----------
    listen_description : description
        description of the dataflow at IP_TX's source.
    description : description
        description of the dataflow.

    Attributes
    ----------
    sink : in
        Packet data without CRC.
    calculator_sink : in
        Packet data without CRC after UDP and IP.
    source : out
        Packet data with CRC.
    """
    def __init__(self, listen_description, description):
        self.sink            = sink            = Endpoint(description)
        self.source          = source          = Endpoint(description)
        self.calculator_sink = calculator_sink = Endpoint(listen_description)

        # # #

        # Parameters.
        data_width  = len(sink.data)
        ratio       = 32//data_width
        assert data_width in [8]

        self.crc_calc = crc_calc = LiteEthInfinibandICRCCalculator(len(calculator_sink.data))

        crc            = Signal(32)
        remembered_crc = Signal(32, reset_less=True)
        rem_crc        = Signal()

        # Combinatorial remembering logic
        self.sync += If(rem_crc,
            remembered_crc.eq(crc_calc.crc_packet)
        )
        self.comb += If(rem_crc,
            crc.eq(crc_calc.crc_packet)
        ).Else(
            crc.eq(remembered_crc)
        )

        self.comb += [
            crc_calc.data.eq(calculator_sink.data),
        ]

        self.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            crc_calc.ce.eq(calculator_sink.valid & calculator_sink.ready),
            sink.connect(source, omit={"last", "ready"}),
            sink.ready.eq(~sink.last & source.ready),
            If(sink.valid & source.ready & sink.last,
                NextValue(rem_crc, 1),
                NextState("CRC")
            )
        )

        if ratio > 1:
            cnt      = Signal(max=ratio, reset=ratio-1)
            cnt_done = Signal()
            fsm.act("CRC",
                source.valid.eq(1),
                crc_calc.reset.eq(1),
                chooser(crc, cnt, source.data, reverse=True),
                NextValue(rem_crc, 0),
                If(cnt_done,
                    source.last.eq(1),
                    If(source.ready,
                        sink.ready.eq(1),
                        NextState("COPY")
                    )
                )
            )
            self.comb += cnt_done.eq(cnt == 0)
            self.sync += \
                If(fsm.ongoing("COPY"),
                    cnt.eq(cnt.reset)
                ).Elif(fsm.ongoing("CRC") & ~cnt_done,
                    cnt.eq(cnt - source.ready)
                )
        else:
            fsm.act("CRC",
                NextValue(rem_crc, 0),
                source.valid.eq(1),
                source.last.eq(1),
                source.data.eq(crc),
                If(source.ready,
                    sink.ready.eq(1),
                    NextState("COPY")
                )
            )

class LiteEthInfinibandICRCChecker(LiteXModule):
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
    calculator_sink : in
        Packet data with CRC before IP_RX.
    source : out
        Packet data without CRC and "error" set to 0
        on last when CRC OK / set to 1 when CRC KO.
    error : out
        Pulses every time a CRC error is detected.
    """
    def __init__(self, listen_description, description):
        self.sink            = sink            = Endpoint(description)
        self.source          = source          = Endpoint(description)
        self.calculator_sink = calculator_sink = Endpoint(listen_description)

        self.valid_crc = valid_crc = Signal()

        # Can be disabled for debug purposes
        self.actual_check = CSRStorage(reset=1)

        # # #

        self.crc_calc = crc_calc = LiteEthInfinibandICRCCalculator(len(calculator_sink.data))

        pipe      = Signal(32, reset_less=True)
        calc_pipe = Signal(32, reset_less=True)

        shift_pipe = Signal()

        self.sync += [
            If(calculator_sink.valid & calculator_sink.ready,
                calc_pipe.eq(Cat(calc_pipe[8:], calculator_sink.data))
            ),
            If(shift_pipe, pipe.eq(Cat(pipe[8:], sink.data)))
        ]

        fill_cnt = Signal(2)
        self.fsm = fsm = FSM(reset_state="FILL")
        fsm.act("FILL",
            sink.ready.eq(1),
            If(sink.valid,
                shift_pipe.eq(1),
                NextValue(fill_cnt, fill_cnt + 1),
                If(fill_cnt == 3,
                    NextState("PASSTHROUGH")
                )
            )
        )

        fsm.act("PASSTHROUGH",
            shift_pipe.eq(sink.valid & source.ready),
            sink.connect(source, keep={"valid", "ready", "last"}),
            If(sink.valid & source.ready & sink.last,
                NextState("FILL")
            )
        )

        calc_fill_cnt = Signal(2)
        self.fsm_calc = fsm_calc = FSM(reset_state="FILL")
        fsm_calc.act("FILL",
            If(calculator_sink.valid & calculator_sink.ready,
                NextValue(calc_fill_cnt, calc_fill_cnt + 1),
                If(calc_fill_cnt == 3,
                    NextState("CALCULATE")
                )
            )
        )

        fsm_calc.act("CALCULATE",
            # Calculator connection
            crc_calc.ce.eq(calculator_sink.valid & calculator_sink.ready),
            If(calculator_sink.valid & calculator_sink.ready & calculator_sink.last,
                crc_calc.reset.eq(1),
                NextState("FILL")
            )
        )

        self.comb += [
            # Passthough connection (with delay)
            source.data.eq(pipe[:8]),

            crc_calc.data.eq(calc_pipe[:8]),

            valid_crc.eq((~self.actual_check.storage) | (crc_calc.crc_packet == pipe))
        ]
