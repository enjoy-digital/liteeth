#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.fsm import *
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect import stream
from litex.soc.interconnect.csr_eventmanager import *
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.common import *

# PCS Gearbox --------------------------------------------------------------------------------------

class PCSGearbox(LiteXModule):
    def __init__(self):
        self.tx_data      = Signal(10)
        self.tx_data_half = Signal(20)
        self.rx_data_half = Signal(20)
        self.rx_data      = Signal(10)

        # # #

        # TX
        buf = Signal(20)
        self.sync.eth_tx += buf.eq(Cat(buf[10:], self.tx_data))
        self.sync.eth_tx_half += self.tx_data_half.eq(buf)

        # RX
        phase_half       = Signal()
        phase_half_rereg = Signal()
        self.sync.eth_rx_half += phase_half_rereg.eq(phase_half)
        self.sync.eth_rx += [
            If(phase_half == phase_half_rereg,
                self.rx_data.eq(self.rx_data_half[10:])
            ).Else(
                self.rx_data.eq(self.rx_data_half[:10])
            ),
            phase_half.eq(~phase_half),
        ]

# PCS SGMII Timer ----------------------------------------------------------------------------------

SGMII_1000MBPS_SPEED = 0b10
SGMII_100MBPS_SPEED  = 0b01
SGMII_10MBPS_SPEED   = 0b00

class PCSSGMIITimer(LiteXModule):
    def __init__(self, speed):
        self.enable = Signal()
        self.done   = Signal()

        # # #

        count = Signal(max=100)
        self.comb += self.done.eq(count == 0)
        self.sync += [
            count.eq(count - 1),
            If(~self.enable | self.done,
                Case(speed, {
                    SGMII_10MBPS_SPEED   : count.eq(99),
                    SGMII_100MBPS_SPEED  : count.eq(9),
                    SGMII_1000MBPS_SPEED : count.eq(0),
                })
            )
        ]

# PCS TX -------------------------------------------------------------------------------------------

class PCSTX(LiteXModule):
    def __init__(self, lsb_first=False):
        self.config_valid = Signal()                               # Config valid.
        self.config_reg   = Signal(16)                             # Config register (16-bit).
        self.sgmii_speed  = Signal(2)                              # SGMII speed.
        self.sink         = sink = stream.Endpoint([("data", 8)])  # Data input.

        self.encoder = Encoder(lsb_first=lsb_first) # 8b/10b Encoder.

        # Signals.
        # --------
        count  = Signal() # Byte counter for config register.
        parity = Signal() # Parity for /R/ extension.
        ctype  = Signal() # Toggles config type.

        # SGMII Timer.
        # ------------
        self.timer = timer = PCSSGMIITimer(speed=self.sgmii_speed)

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            self.encoder.k[0].eq(1),
            self.encoder.d[0].eq(K(28, 5)),
            # Wait for valid Config.
            If(self.config_valid,
                NextValue(count, 0),
                NextState("CONFIG-D")
            # Wait for valid Data.
            ).Else(
                If(sink.valid,
                    sink.ready.eq(timer.done),
                    self.encoder.d[0].eq(K(27, 7)), # Start-of-packet /S/.
                    NextState("DATA")
                ).Else(
                    NextState("IDLE")
                )
            )
        )
        fsm.act("CONFIG-D",
            # Send Configuration Word.
            Case(ctype, {
                0b0 : self.encoder.d[0].eq(D(21, 5)), # /C1/.
                0b1 : self.encoder.d[0].eq(D( 2, 2)), # /C2/.
            }),
            NextValue(ctype, ~ctype),
            NextState("CONFIG-REG")
        ),
        fsm.act("CONFIG-REG",
            # Send Configuration Register.
            NextValue(count, count + 1),
            Case(count, {
                0 : self.encoder.d[0].eq(self.config_reg[:8]), # LSB.
                1 : self.encoder.d[0].eq(self.config_reg[8:]), # MSB.
            }),
            If(count == (2 - 1), NextState("START"))
        )
        fsm.act("IDLE",
            # Send Idle words and handle disparity.
            Case(self.encoder.disparity[0], {
                0b0 : self.encoder.d[0].eq(D(5, 6)),   # /I1/ (Preserves disparity).
                0b1 : self.encoder.d[0].eq(D(16, 2)),  # /I2/ (Flips disparity).
            }),
            NextState("START")
        )
        fsm.act("DATA",
            # Send Data.
            timer.enable.eq(1),
            sink.ready.eq(timer.done),
            If(sink.valid,
                self.encoder.d[0].eq(sink.data),
            ).Else(
                self.encoder.k[0].eq(1),
                self.encoder.d[0].eq(K(29, 7)), # End-of-frame /T/.
                NextState("CARRIER-EXTEND")
            )
        )
        fsm.act("CARRIER-EXTEND",
            # Extend carrier with /R/ symbols.
            self.encoder.k[0].eq(1),
            self.encoder.d[0].eq(K(23, 7)), # Carrier Extend /R/.
            If(parity,
                NextState("START")
            )
        )
        self.sync += parity.eq(~parity) # Toggle parity for /R/ extension.

# PCS RX -------------------------------------------------------------------------------------------

class PCSRX(LiteXModule):
    def __init__(self, lsb_first=False):
        self.seen_valid_ci   = Signal()   # CI seen.
        self.seen_config_reg = Signal()   # Config seen.
        self.config_reg      = Signal(16) # Config register (16-bit).
        self.sgmii_speed     = Signal(2)  # SGMII speed.
        self.source          = source = stream.Endpoint([("data", 8), ("error", 1)]) # Data output.

        self.decoder = Decoder(lsb_first=lsb_first) # 8b/10b Decoder.

        # # #

        # Signals.
        # --------
        count = Signal() # Byte counter for config register.

        # SGMII Timer.
        # ------------
        self.timer = timer = CEInserter()(PCSSGMIITimer(speed=self.sgmii_speed))
        self.comb += timer.ce.eq(self.decoder.ce)

        # Buffer.
        # -------
        self.buffer = buffer = stream.Buffer([("data", 8)], pipe_valid=True, pipe_ready=False)
        self.comb += If(timer.ce & timer.done,
            buffer.source.connect(source, omit={"last", "error"}),
            source.last.eq(buffer.source.valid & ~buffer.sink.valid), # Last when next is not valid.
        )

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            If(self.decoder.ce,
                # Wait for a K-character.
                If(self.decoder.k,
                    # K-character is Config or Idle K28.5.
                    If(self.decoder.d == K(28, 5),
                        NextValue(count, 0),
                        NextState("CONFIG-D-OR-IDLE")
                    ),
                    # K-character is Start-of-packet /S/.
                    If(self.decoder.d == K(27, 7),
                        timer.enable.eq(1),
                        buffer.sink.valid.eq(1),
                        buffer.sink.data.eq(0x55), # First Preamble Byte.
                        NextState("DATA")
                    )
                )
            )
        )
        fsm.act("CONFIG-D-OR-IDLE",
            If(self.decoder.ce,
                If(~self.decoder.k & ~self.decoder.invalid,
                    # Check for Configuration Word.
                    If((self.decoder.d == D(21, 5)) | # /C1/.
                       (self.decoder.d == D( 2, 2)),  # /C2/.
                        self.seen_valid_ci.eq(1),
                        NextState("CONFIG-REG")
                    ),
                    # Check for Idle Word.
                    If((self.decoder.d == D( 5, 6)) | # /I1/.
                       (self.decoder.d == D(16, 2)),  # /I2/.
                        self.seen_valid_ci.eq(1),
                        NextState("START")
                    )
                ).Else(
                    NextState("ERROR"),
                )
            )
        )
        fsm.act("CONFIG-REG",
            If(self.decoder.ce,
                If(~self.decoder.k & ~self.decoder.invalid,
                    # Receive for Configuration Register.
                    NextValue(count, count + 1),
                    Case(count, {
                        0b0 : NextValue(self.config_reg[:8], self.decoder.d), # LSB.
                        0b1 : NextValue(self.config_reg[8:], self.decoder.d), # MSB.
                    }),
                    If(count == (2 - 1),
                        self.seen_config_reg.eq(1),
                        NextState("START")
                    )
                ).Else(
                    NextState("ERROR"),
                )
            )
        )
        fsm.act("DATA",
            If(self.decoder.ce,
                If(~self.decoder.k & ~self.decoder.invalid,
                    # Receive Data.
                    timer.enable.eq(1),
                    buffer.sink.valid.eq(timer.done),
                    buffer.sink.data.eq(self.decoder.d),
                ).Elif(self.decoder.k & (self.decoder.d == K(29, 7)) & ~self.decoder.invalid,
                    # K-character is End-of-packet /S/.
                    NextState("START"),
                ).Else(
                    source.error.eq(1),
                    source.last.eq(1),
                    source.valid.eq(1),
                    If(source.ready,
                       NextState("ERROR"),
                    )
                )
            )
        )
        fsm.act("ERROR",
            NextState("START")
        )

# Four-Symbol PCS TX -------------------------------------------------------------------------------

class PCSTX4(LiteXModule):
    """Four-symbol/cycle 1000BASE-X-style transmitter.

    This keeps the Clause-36 code-group stream used by :class:`PCSTX`, but
    emits four 8b/10b symbols per clock.  It is intended for experimental
    5Gb/s MAC operation over a 6.25Gb/s serial link with a 40-bit GTP
    interface.
    """
    def __init__(self, lsb_first=False):
        self.config_valid = Signal()
        self.config_reg   = Signal(16)
        self.sgmii_speed  = Signal(2)
        self.sink         = sink = stream.Endpoint(eth_phy_description(32))

        self.encoder = Encoder(nwords=4, lsb_first=lsb_first)

        # # #

        ctype = Signal()
        d     = self.encoder.d
        k     = self.encoder.k

        # Two legal idle ordered sets.  Keeping the pair boundaries at lanes
        # 0/2 also makes /S/ naturally start on an even code-group boundary.
        self.comb += [
            d[0].eq(K(28, 5)), k[0].eq(1),
            d[1].eq(D( 5, 6)), k[1].eq(0),
            d[2].eq(K(28, 5)), k[2].eq(1),
            d[3].eq(D( 5, 6)), k[3].eq(0),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.config_valid,
                d[0].eq(K(28, 5)), k[0].eq(1),
                d[1].eq(Mux(ctype, D(2, 2), D(21, 5))), k[1].eq(0),
                d[2].eq(self.config_reg[:8]),            k[2].eq(0),
                d[3].eq(self.config_reg[8:]),            k[3].eq(0),
                NextValue(ctype, ~ctype),
            ).Elif(sink.valid,
                sink.ready.eq(1),
                # /S/ replaces the first preamble byte, as in the 8-bit PCS.
                d[0].eq(K(27, 7)),       k[0].eq(1),
                d[1].eq(sink.data[8:16]), k[1].eq(0),
                d[2].eq(sink.data[16:24]), k[2].eq(0),
                d[3].eq(sink.data[24:32]), k[3].eq(0),
                If(sink.last,
                    Case(sink.last_be, {
                        0b0001 : [
                            d[1].eq(K(29, 7)), k[1].eq(1),
                            d[2].eq(K(23, 7)), k[2].eq(1),
                            d[3].eq(K(23, 7)), k[3].eq(1),
                        ],
                        0b0010 : [
                            d[2].eq(K(29, 7)), k[2].eq(1),
                            d[3].eq(K(23, 7)), k[3].eq(1),
                        ],
                        0b0100 : [
                            d[3].eq(K(29, 7)), k[3].eq(1),
                            NextState("EXTEND-ODD"),
                        ],
                        0b1000 : NextState("TERMINATE-EVEN"),
                    })
                ).Else(
                    NextState("DATA")
                )
            )
        )
        fsm.act("DATA",
            If(sink.valid,
                sink.ready.eq(1),
                d[0].eq(sink.data[ 0: 8]), k[0].eq(0),
                d[1].eq(sink.data[ 8:16]), k[1].eq(0),
                d[2].eq(sink.data[16:24]), k[2].eq(0),
                d[3].eq(sink.data[24:32]), k[3].eq(0),
                If(sink.last,
                    Case(sink.last_be, {
                        0b0001 : [
                            d[1].eq(K(29, 7)), k[1].eq(1),
                            d[2].eq(K(23, 7)), k[2].eq(1),
                            d[3].eq(K(23, 7)), k[3].eq(1),
                            NextState("IDLE"),
                        ],
                        0b0010 : [
                            d[2].eq(K(29, 7)), k[2].eq(1),
                            d[3].eq(K(23, 7)), k[3].eq(1),
                            NextState("IDLE"),
                        ],
                        0b0100 : [
                            d[3].eq(K(29, 7)), k[3].eq(1),
                            NextState("EXTEND-ODD"),
                        ],
                        0b1000 : NextState("TERMINATE-EVEN"),
                    })
                )
            ).Else(
                # A gap inside a frame is not representable on the wire. End
                # the frame deterministically and let the RX error accounting
                # expose any upstream contract violation.
                d[0].eq(K(29, 7)), k[0].eq(1),
                d[1].eq(K(23, 7)), k[1].eq(1),
                d[2].eq(K(28, 5)), k[2].eq(1),
                d[3].eq(D( 5, 6)), k[3].eq(0),
                NextState("IDLE"),
            )
        )
        # /T/ in lane 3 needs two /R/ code groups before the next even-aligned
        # idle pair. /T/ after lane 3 starts the following cycle in lane 0.
        fsm.act("EXTEND-ODD",
            d[0].eq(K(23, 7)), k[0].eq(1),
            d[1].eq(K(23, 7)), k[1].eq(1),
            d[2].eq(K(28, 5)), k[2].eq(1),
            d[3].eq(D( 5, 6)), k[3].eq(0),
            NextState("IDLE"),
        )
        fsm.act("TERMINATE-EVEN",
            d[0].eq(K(29, 7)), k[0].eq(1),
            d[1].eq(K(23, 7)), k[1].eq(1),
            d[2].eq(K(28, 5)), k[2].eq(1),
            d[3].eq(D( 5, 6)), k[3].eq(0),
            NextState("IDLE"),
        )


# Four-Symbol PCS RX -------------------------------------------------------------------------------

class PCSRX4(LiteXModule):
    """Four-symbol/cycle receiver with arbitrary code-group phase support."""
    def __init__(self, lsb_first=False):
        self.seen_valid_ci   = Signal()
        self.seen_config_reg = Signal()
        self.config_reg      = Signal(16)
        self.sgmii_speed     = Signal(2)
        self.input           = Signal(40)
        self.source          = source = stream.Endpoint(eth_phy_description(32))
        self.overflow        = Signal()
        self.code_error      = Signal(4)
        self.disparity_error = Signal(4)

        self.decoders = decoders = [Decoder(lsb_first=lsb_first, sync=False) for _ in range(4)]
        self.submodules += decoders
        for lane, decoder in enumerate(decoders):
            self.comb += decoder.input.eq(self.input[10*lane:10*(lane + 1)])

        # Track received running disparity across all four symbols. Decoder's
        # invalid output catches malformed population counts; this additionally
        # reports a legal positive/negative symbol received with the wrong RD.
        rx_disparity = Signal()
        disparities  = [Signal() for _ in range(5)]
        self.comb += disparities[0].eq(rx_disparity)
        for lane in range(4):
            ones = Signal(4)
            self.comb += [
                ones.eq(Reduce("ADD", [self.input[10*lane + bit] for bit in range(10)])),
                self.code_error[lane].eq(decoders[lane].invalid),
                self.disparity_error[lane].eq(
                    ((ones > 5) & disparities[lane]) |
                    ((ones < 5) & ~disparities[lane])
                ),
                disparities[lane + 1].eq(Mux(
                    ones == 5,
                    disparities[lane],
                    ones > 5,
                )),
            ]
        self.sync += rx_disparity.eq(disparities[4])

        # Register decoded symbols once before the parallel control parser.
        # Besides matching the packet aligner's existing history window, this
        # prevents a four-symbol Clause-37 parse from becoming a direct GTP to
        # state-register path at the 156.25MHz experimental 5G PCS rate.
        prev_d       = [Signal(8) for _ in range(4)]
        prev_k       = [Signal() for _ in range(4)]
        prev_invalid = [Signal() for _ in range(4)]
        for lane, decoder in enumerate(decoders):
            self.sync += [
                prev_d[lane].eq(decoder.d),
                prev_k[lane].eq(decoder.k),
                prev_invalid[lane].eq(decoder.invalid),
            ]

        # Parallel Clause-37 configuration/idle parser.  The four combinatorial
        # steps preserve parser state across 40-bit boundaries.
        CTRL_K, CTRL_C_OR_IDLE, CTRL_LO, CTRL_HI = range(4)
        ctrl_state = Signal(2)
        ctrl_reg   = Signal(16)
        states     = [Signal(2) for _ in range(5)]
        regs       = [Signal(16) for _ in range(5)]
        seen_ci    = [Signal() for _ in range(4)]
        seen_cfg   = [Signal() for _ in range(4)]
        self.ctrl_state = ctrl_state
        self.comb += [states[0].eq(ctrl_state), regs[0].eq(ctrl_reg)]
        for lane in range(4):
            data    = prev_d[lane]
            is_k    = prev_k[lane]
            invalid = prev_invalid[lane]
            self.comb += [
                states[lane + 1].eq(CTRL_K),
                regs[lane + 1].eq(regs[lane]),
                seen_ci[lane].eq(0),
                seen_cfg[lane].eq(0),
                Case(states[lane], {
                    CTRL_K : If(is_k & (data == K(28, 5)) & ~invalid,
                        states[lane + 1].eq(CTRL_C_OR_IDLE)
                    ),
                    CTRL_C_OR_IDLE : If(~is_k & ~invalid,
                        If((data == D(21, 5)) | (data == D(2, 2)),
                            states[lane + 1].eq(CTRL_LO),
                            seen_ci[lane].eq(1),
                        ).Elif((data == D(5, 6)) | (data == D(16, 2)),
                            states[lane + 1].eq(CTRL_K),
                            seen_ci[lane].eq(1),
                        )
                    ).Elif(is_k & (data == K(28, 5)) & ~invalid,
                        states[lane + 1].eq(CTRL_C_OR_IDLE)
                    ),
                    CTRL_LO : If(~is_k & ~invalid,
                        regs[lane + 1][:8].eq(data),
                        states[lane + 1].eq(CTRL_HI),
                    ),
                    CTRL_HI : If(~is_k & ~invalid,
                        regs[lane + 1][8:].eq(data),
                        states[lane + 1].eq(CTRL_K),
                        seen_cfg[lane].eq(1),
                    ),
                })
            ]
        self.sync += [
            ctrl_state.eq(states[4]),
            ctrl_reg.eq(regs[4]),
            self.config_reg.eq(regs[4]),
        ]
        self.comb += [
            self.seen_valid_ci.eq(Cat(*seen_ci) != 0),
            self.seen_config_reg.eq(Cat(*seen_cfg) != 0),
        ]

        # Keep one previous decoded word.  A phase-selectable 8-symbol window
        # then presents packet bytes in 32-bit MAC word order for any /S/ lane.
        start_found = Signal()
        start_phase = Signal(2)
        start_conditions = [
            decoder.k & (decoder.d == K(27, 7)) & ~decoder.invalid
            for decoder in decoders
        ]
        self.comb += [
            start_found.eq(0),
            start_phase.eq(0),
            If(start_conditions[0], start_found.eq(1), start_phase.eq(0)
            ).Elif(start_conditions[1], start_found.eq(1), start_phase.eq(1)
            ).Elif(start_conditions[2], start_found.eq(1), start_phase.eq(2)
            ).Elif(start_conditions[3], start_found.eq(1), start_phase.eq(3))
        ]

        in_packet    = Signal()
        first_word   = Signal()
        phase        = Signal(2)
        self.in_packet  = in_packet
        self.first_word = first_word
        self.phase      = phase
        window_valid = Signal()
        window_d     = [Signal(8) for _ in range(4)]
        window_k     = [Signal() for _ in range(4)]
        window_bad   = [Signal() for _ in range(4)]

        first_cases = {
            0 : [
                window_d[0].eq(0x55), window_k[0].eq(0), window_bad[0].eq(0),
                window_d[1].eq(prev_d[1]), window_k[1].eq(prev_k[1]), window_bad[1].eq(prev_invalid[1]),
                window_d[2].eq(prev_d[2]), window_k[2].eq(prev_k[2]), window_bad[2].eq(prev_invalid[2]),
                window_d[3].eq(prev_d[3]), window_k[3].eq(prev_k[3]), window_bad[3].eq(prev_invalid[3]),
            ],
            1 : [
                window_d[0].eq(0x55), window_k[0].eq(0), window_bad[0].eq(0),
                window_d[1].eq(prev_d[2]), window_k[1].eq(prev_k[2]), window_bad[1].eq(prev_invalid[2]),
                window_d[2].eq(prev_d[3]), window_k[2].eq(prev_k[3]), window_bad[2].eq(prev_invalid[3]),
                window_d[3].eq(decoders[0].d), window_k[3].eq(decoders[0].k), window_bad[3].eq(decoders[0].invalid),
            ],
            2 : [
                window_d[0].eq(0x55), window_k[0].eq(0), window_bad[0].eq(0),
                window_d[1].eq(prev_d[3]), window_k[1].eq(prev_k[3]), window_bad[1].eq(prev_invalid[3]),
                window_d[2].eq(decoders[0].d), window_k[2].eq(decoders[0].k), window_bad[2].eq(decoders[0].invalid),
                window_d[3].eq(decoders[1].d), window_k[3].eq(decoders[1].k), window_bad[3].eq(decoders[1].invalid),
            ],
            3 : [
                window_d[0].eq(0x55), window_k[0].eq(0), window_bad[0].eq(0),
                window_d[1].eq(decoders[0].d), window_k[1].eq(decoders[0].k), window_bad[1].eq(decoders[0].invalid),
                window_d[2].eq(decoders[1].d), window_k[2].eq(decoders[1].k), window_bad[2].eq(decoders[1].invalid),
                window_d[3].eq(decoders[2].d), window_k[3].eq(decoders[2].k), window_bad[3].eq(decoders[2].invalid),
            ],
        }
        steady_cases = {}
        for p in range(4):
            symbols = list(zip(prev_d[p:], prev_k[p:], prev_invalid[p:])) + [
                (decoder.d, decoder.k, decoder.invalid) for decoder in decoders[:p]
            ]
            steady_cases[p] = []
            for lane, (data, is_k, invalid) in enumerate(symbols):
                steady_cases[p] += [
                    window_d[lane].eq(data),
                    window_k[lane].eq(is_k),
                    window_bad[lane].eq(invalid),
                ]

        self.comb += [
            window_valid.eq(in_packet),
            *[window_d[n].eq(0) for n in range(4)],
            *[window_k[n].eq(0) for n in range(4)],
            *[window_bad[n].eq(0) for n in range(4)],
            If(first_word,
                Case(phase, first_cases)
            ).Else(
                Case(phase, steady_cases)
            )
        ]

        # Find the first /T/ or malformed symbol in the aligned word.
        word_data  = Signal(32)
        word_term  = Signal()
        word_error = Signal()
        word_count = Signal(3, reset=4)
        self.comb += [
            word_data.eq(Cat(*window_d)),
            word_term.eq(0),
            word_error.eq(0),
            word_count.eq(4),
            If(window_bad[0] | window_k[0],
                word_term.eq(1), word_count.eq(0),
                word_error.eq(window_bad[0] | (window_d[0] != K(29, 7))),
            ).Elif(window_bad[1] | window_k[1],
                word_term.eq(1), word_count.eq(1),
                word_error.eq(window_bad[1] | (window_d[1] != K(29, 7))),
            ).Elif(window_bad[2] | window_k[2],
                word_term.eq(1), word_count.eq(2),
                word_error.eq(window_bad[2] | (window_d[2] != K(29, 7))),
            ).Elif(window_bad[3] | window_k[3],
                word_term.eq(1), word_count.eq(3),
                word_error.eq(window_bad[3] | (window_d[3] != K(29, 7))),
            )
        ]

        # A held word supplies look-ahead for /T/ in lane zero.  Partial final
        # words are emitted on the following idle cycle, which the Ethernet IFG
        # always provides.
        hold_valid   = Signal()
        hold_last    = Signal()
        hold_data    = Signal(32)
        hold_last_be = Signal(4)
        hold_error   = Signal(4)

        emit_valid   = Signal()
        emit_data    = Signal(32)
        emit_last    = Signal()
        emit_last_be = Signal(4)
        emit_error   = Signal(4)
        count_to_be  = Array([0, 0b0001, 0b0010, 0b0100, 0b1000])
        self.comb += [
            emit_valid.eq(0), emit_data.eq(0), emit_last.eq(0),
            emit_last_be.eq(0), emit_error.eq(0),
            If(hold_last,
                emit_valid.eq(hold_valid),
                emit_data.eq(hold_data),
                emit_last.eq(1),
                emit_last_be.eq(hold_last_be),
                emit_error.eq(hold_error),
            ).Elif(window_valid,
                If(~word_term,
                    If(hold_valid,
                        emit_valid.eq(1),
                        emit_data.eq(hold_data),
                        emit_error.eq(hold_error),
                    )
                ).Elif(word_count == 0,
                    If(hold_valid,
                        emit_valid.eq(1),
                        emit_data.eq(hold_data),
                        emit_last.eq(1),
                        emit_last_be.eq(0b1000),
                        emit_error.eq(hold_error | Mux(word_error, 0b1000, 0)),
                    )
                ).Else(
                    If(hold_valid,
                        emit_valid.eq(1),
                        emit_data.eq(hold_data),
                        emit_error.eq(hold_error),
                    )
                )
            )
        ]

        self.fifo = fifo = stream.SyncFIFO(eth_phy_description(32), depth=8, buffered=True)
        self.comb += [
            fifo.sink.valid.eq(emit_valid),
            fifo.sink.data.eq(emit_data),
            fifo.sink.last.eq(emit_last),
            fifo.sink.last_be.eq(emit_last_be),
            fifo.sink.error.eq(emit_error),
            fifo.source.connect(source),
        ]

        self.sync += [
            If(start_found & ~in_packet & ~hold_last,
                in_packet.eq(1),
                first_word.eq(1),
                phase.eq(start_phase),
            ).Elif(window_valid,
                first_word.eq(0),
                If(word_term, in_packet.eq(0)),
            ),
            If(hold_last,
                hold_valid.eq(0),
                hold_last.eq(0),
            ).Elif(window_valid,
                If(~word_term,
                    hold_valid.eq(1),
                    hold_last.eq(0),
                    hold_data.eq(word_data),
                    hold_last_be.eq(0),
                    hold_error.eq(0),
                ).Elif(word_count == 0,
                    hold_valid.eq(0),
                    hold_last.eq(0),
                ).Else(
                    hold_valid.eq(1),
                    hold_last.eq(1),
                    hold_data.eq(word_data),
                    hold_last_be.eq(count_to_be[word_count]),
                    hold_error.eq(Mux(word_error, count_to_be[word_count], 0)),
                )
            ),
            If(emit_valid & ~fifo.sink.ready,
                self.overflow.eq(1)
            )
        ]

# PCS ----------------------------------------------------------------------------------------------

class PCS(LiteXModule):
    autocsr_exclude = {"ev"}
    def __init__(self, lsb_first=False, check_period=6e-3, breaklink_time=10e-3,
        more_ack_time=10e-3, sgmii_ack_time=1.6e-3, eth_tx_clk_freq=125e6,
        with_csr=False, dw=8):
        if dw not in [8, 32]:
            raise ValueError("1000BASE-X PCS data width must be 8 or 32 bits")
        tx_cls  = {8: PCSTX, 32: PCSTX4}[dw]
        rx_cls  = {8: PCSRX, 32: PCSRX4}[dw]
        self.tx = ClockDomainsRenamer("eth_tx")(tx_cls(lsb_first=lsb_first))
        self.rx = ClockDomainsRenamer("eth_rx")(rx_cls(lsb_first=lsb_first))

        self.tbi_tx = Signal(10*(dw//8))
        self.tbi_rx = Signal(10*(dw//8))
        if dw == 8:
            self.comb += [
                self.tbi_tx.eq(self.tx.encoder.output[0]),
                self.rx.decoder.input.eq(self.tbi_rx),
            ]
            self.tbi_rx_ce = self.rx.decoder.ce
        else:
            self.comb += [
                self.tbi_tx.eq(Cat(*self.tx.encoder.output)),
                self.rx.input.eq(self.tbi_rx),
            ]
            self.tbi_rx_ce = Signal(reset=1)
        self.sink      = stream.Endpoint(eth_phy_description(dw))
        self.source    = stream.Endpoint(eth_phy_description(dw))

        self.link_up = Signal()
        self.restart = Signal()
        self.align   = Signal()

        self.lp_abi = BusSynchronizer(16, "eth_rx", "eth_tx")

        # # #

        # Signals.
        # --------
        self.config_empty = config_empty = Signal()
        self.is_sgmii     = is_sgmii     = Signal()
        self.linkdown     = linkdown     = Signal()
        self.autoneg_ack  = autoneg_ack  = Signal()

        # Sink -> TX / RX -> Source.
        if dw == 8:
            self.comb += [
                self.sink.connect(self.tx.sink,     omit={"last_be", "error"}),
                self.rx.source.connect(self.source, omit={"last_be"}),
            ]
        else:
            self.comb += [
                self.sink.connect(self.tx.sink, omit={"error"}),
                self.rx.source.connect(self.source),
            ]

        # Pulse Synchronizers.
        # --------------------
        self.seen_valid_ci     = seen_valid_ci     = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_abi = rx_config_reg_abi = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_ack = rx_config_reg_ack = PulseSynchronizer("eth_rx", "eth_tx")
        self.comb += seen_valid_ci.i.eq(self.rx.seen_valid_ci)

        # Timers.
        # -------
        self.breaklink_timer = breaklink_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(breaklink_time * eth_tx_clk_freq))
        self.more_ack_timer  = more_ack_timer  = ClockDomainsRenamer("eth_tx")(WaitTimer(more_ack_time  * eth_tx_clk_freq))
        self.sgmii_ack_timer = sgmii_ack_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(sgmii_ack_time * eth_tx_clk_freq))

        # Checker.
        # --------
        checker_max   = int(check_period*eth_tx_clk_freq)
        checker_count = Signal(max=checker_max + 1)
        checker_tick  = Signal()
        checker_error = Signal()
        self.sync.eth_tx += [
            checker_tick.eq(0),
            If(checker_count == 0,
                checker_tick.eq(1),
                checker_count.eq(checker_max)
            ).Else(
                checker_count.eq(checker_count - 1)
            ),
            If(seen_valid_ci.o, checker_error.eq(0)),
            If(checker_tick,    checker_error.eq(1))
        ]

        # Linkdown/Speed Detection.
        # -------------------------
        sgmii_speed_valid = Signal()
        sgmii_tx_speed    = Signal(2)
        sgmii_rx_speed    = Signal(2)
        self.comb += [
            is_sgmii.eq(self.lp_abi.o[0]),
            sgmii_speed_valid.eq(self.lp_abi.o[10:12] != 0b11),
            sgmii_tx_speed.eq(Mux(sgmii_speed_valid, self.lp_abi.o[10:12], SGMII_1000MBPS_SPEED)),
            sgmii_rx_speed.eq(Mux(self.lp_abi.i[10:12] != 0b11, self.lp_abi.i[10:12], SGMII_1000MBPS_SPEED)),
            # Detect that link is down:
            # - 1000BASE-X : linkup can be inferred by non-empty reg.
            # - SGMII      : linkup is indicated with bit 15.
            If(~is_sgmii,
                linkdown.eq(self.lp_abi.o == 0),
                self.tx.sgmii_speed.eq(0b10),
                self.rx.sgmii_speed.eq(0b10),
            ).Else(
                linkdown.eq(~self.lp_abi.o[15] | ~sgmii_speed_valid),
                self.tx.sgmii_speed.eq(sgmii_tx_speed),
                self.rx.sgmii_speed.eq(sgmii_rx_speed),
            )
        ]

        # TX Config.
        # ----------
        self.comb += [
            If(~config_empty,
                self.tx.config_reg[0].eq(is_sgmii),                     # SGMII: SGMII in-use.
                self.tx.config_reg[5].eq(~is_sgmii),                    # 1000BASE-X: Full-duplex.
                If(is_sgmii,
                    self.tx.config_reg[10:12].eq(sgmii_tx_speed),       # SGMII: Speed.
                    self.tx.config_reg[12].eq(1),                       # SGMII: Full-duplex.
                    self.tx.config_reg[15].eq(self.link_up),            # SGMII: Link-up.
                ),
                self.tx.config_reg[14].eq(autoneg_ack),                 # SGMII/1000BASE-X: Acknowledge Bit.
            )
        ]

        # FSM.
        # ----
        self.fsm = fsm = ClockDomainsRenamer("eth_tx")(FSM())
        # AN_ENABLE.
        fsm.act("AUTONEG-BREAKLINK",
            self.tx.config_valid.eq(1),
            config_empty.eq(1),
            breaklink_timer.wait.eq(1),
            If(breaklink_timer.done,
                NextState("AUTONEG-WAIT-ABI")
            )
        )
        # ABILITY_DETECT.
        fsm.act("AUTONEG-WAIT-ABI",
            self.align.eq(1),
            self.tx.config_valid.eq(1),
            If(rx_config_reg_abi.o,
                NextState("AUTONEG-WAIT-ACK")
            ),
            If(checker_tick & checker_error,
                self.restart.eq(1),
                NextState("AUTONEG-BREAKLINK")
            )
        )
        # ACKNOWLEDGE_DETECT.
        fsm.act("AUTONEG-WAIT-ACK",
            self.tx.config_valid.eq(1),
            autoneg_ack.eq(1),
            If(rx_config_reg_ack.o,
                NextState("AUTONEG-SEND-MORE-ACK")
            ),
            If(checker_tick & checker_error,
                self.restart.eq(1),
                NextState("AUTONEG-BREAKLINK")
            )
        )
        # COMPLETE_ACKNOWLEDGE.
        fsm.act("AUTONEG-SEND-MORE-ACK",
            self.tx.config_valid.eq(1),
            autoneg_ack.eq(1),
            more_ack_timer.wait.eq(~is_sgmii),
            sgmii_ack_timer.wait.eq(is_sgmii),
            If((is_sgmii & sgmii_ack_timer.done) |
                (~is_sgmii & more_ack_timer.done),
                NextState("RUNNING")
            ),
            If(checker_tick & checker_error,
                self.restart.eq(1),
                NextState("AUTONEG-BREAKLINK")
            )
        )
        # LINK_OK.
        fsm.act("RUNNING",
            self.link_up.eq(~linkdown),
            If((checker_tick & checker_error) | linkdown,
                self.restart.eq(1),
                NextState("AUTONEG-BREAKLINK")
            )
        )

        # RX Config (and consistency check).
        # ----------------------------------
        rx_config_reg_count  = Signal(4)
        rx_config_reg_last   = Signal(16)
        self.sync.eth_rx += [
            If(self.rx.seen_config_reg,
                # Consistency Count/Check.
                rx_config_reg_last.eq(self.rx.config_reg),
                If(self.rx.config_reg != rx_config_reg_last,
                    rx_config_reg_count.eq(8 - 1)
                ).Else(
                    If(rx_config_reg_count != 0,
                        rx_config_reg_count.eq(rx_config_reg_count - 1),
                    ).Else(
                        # When RX Config is consistent.
                        # Acknowledgement.
                        If(self.rx.config_reg[14],
                            rx_config_reg_ack.i.eq(1),
                        # Ability match.
                        ).Else(
                            rx_config_reg_abi.i.eq(1),
                        )
                    )
                ),
                self.lp_abi.i.eq(self.rx.config_reg)
            )
        ]

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self.status = CSRStatus(fields=[
            CSRField("link_up",    size=1,  offset=0,  description="Link is up."),
            CSRField("is_sgmii",   size=1,  offset=1,  description="SGMII in-use."),
            CSRField("config_reg", size=16, offset=16, description="Link partner ability register."),
        ])

        self.lp_abi_csr = BusSynchronizer(16, "eth_rx", "sys")

        self.ev      = EventManager()
        self.ev.link = EventSourceProcess(edge="any")
        self.ev.finalize()

        self.comb += [
            self.lp_abi_csr.i.eq(self.lp_abi.i),
            self.status.fields.config_reg.eq(self.lp_abi_csr.o)
        ]

        self.sync += [
            self.status.fields.link_up.eq(self.link_up),
            self.status.fields.is_sgmii.eq(self.is_sgmii),
        ]

        self.link_up_timer = link_up_timer = WaitTimer(int(LiteXContext.top.sys_clk_freq))

        self.csr_fsm = fsm = FSM()
        fsm.act("DOWN",
            If(self.link_up,
                NextState("UP")
            )
        )
        fsm.act("UP",
            link_up_timer.wait.eq(1),
            self.ev.link.trigger.eq(link_up_timer.done),
            If(~self.link_up,
                NextState("DOWN"),
            )
        )
