#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.fsm import *
from migen.genlib.cdc import MultiReg, PulseSynchronizer

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect import stream
from litex.soc.interconnect.csr_eventmanager import *
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.common import *

# Retain the established PCSGearbox import while keeping raw symbol packing in the PMA layer.
from liteeth.phy.serial.basex.pma.gearbox import PCSGearbox

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

# PCS TX 8-bit -------------------------------------------------------------------------------------

class PCSTX8b(LiteXModule):
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

# PCS RX 8-bit -------------------------------------------------------------------------------------

class PCSRX8b(LiteXModule):
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

# Code-Groups --------------------------------------------------------------------------------------

K28_5 = K(28, 5) # Comma, first code-group of /I/ and /C/.
K27_7 = K(27, 7) # Start-of-packet /S/.
K29_7 = K(29, 7) # End-of-packet /T/.
K23_7 = K(23, 7) # Carrier extend /R/.
D5_6  = D( 5, 6) # /I1/.
D16_2 = D(16, 2) # /I2/.
D21_5 = D(21, 5) # /C1/.
D2_2  = D( 2, 2) # /C2/.

# PCS TX 32-bit (Hard 8b/10b) ----------------------------------------------------------------------

class PCSTX32b(LiteXModule):
    """TX datapath for transceivers with a hard 8b/10b encoder (4 code-groups per cycle, lane 0
    first). The transceiver handles the running disparity."""
    def __init__(self):
        self.config_valid = Signal()   # Config valid.
        self.config_reg   = Signal(16) # Config register (16-bit).
        self.sgmii_speed  = Signal(2)  # Unused (no SGMII rate adaptation).
        self.sink         = sink = stream.Endpoint(eth_phy_description(32)) # Data input.

        # Transceiver interface (registered).
        self.data      = Signal(32) # Code-groups (lane 0 first).
        self.char_is_k = Signal(4)  # K-character flags.

        # # #

        # Signals.
        # --------
        ctype     = Signal()   # Toggles config type.
        data      = Signal(32)
        char_is_k = Signal(4)

        # Idle /I1/ /I2/: K28.5 and D16.2 flip the running disparity, D5.6 does not, so the hard
        # encoder's running disparity settles to negative whatever its state after a packet.
        idle   = Cat(C(K28_5, 8), C(D5_6, 8), C(K28_5, 8), C(D16_2, 8))
        idle_k = 0b0101

        # Output Register.
        # ----------------
        # Two register stages ease routing to the transceiver.
        data_r      = Signal(32)
        char_is_k_r = Signal(4)
        for s in [data_r, char_is_k_r, self.data, self.char_is_k]:
            s.attr.add("keep")
        self.sync += [
            data_r.eq(data),
            char_is_k_r.eq(char_is_k),
            self.data.eq(data_r),
            self.char_is_k.eq(char_is_k_r),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            # Send Configuration Word.
            If(self.config_valid,
                NextValue(ctype, ~ctype),
                char_is_k.eq(0b0001),
                data.eq(Cat(
                    C(K28_5, 8),
                    Mux(ctype, C(D2_2, 8), C(D21_5, 8)), # /C2/ or /C1/.
                    self.config_reg,
                )),
            # Send Start-of-packet /S/ (replaces the first preamble byte).
            ).Elif(sink.valid,
                sink.ready.eq(1),
                char_is_k.eq(0b0001),
                data.eq(Cat(C(K27_7, 8), sink.data[8:])),
                NextState("DATA"),
            # Send Idle.
            ).Else(
                char_is_k.eq(idle_k),
                data.eq(idle),
            )
        )
        fsm.act("DATA",
            sink.ready.eq(1),
            If(sink.valid & ~sink.last,
                # Send Data.
                char_is_k.eq(0b0000),
                data.eq(sink.data),
            ).Elif(sink.valid,
                # Send last Data and End-of-packet: /T/R/ or /T/R/R/ so that the next ordered
                # set starts on an even code-group.
                If(~sink.be[1],
                    char_is_k.eq(0b1110),
                    data.eq(Cat(sink.data[:8], C(K29_7, 8), C(K23_7, 8), C(K23_7, 8))),
                    NextState("START"),
                ).Elif(~sink.be[2],
                    char_is_k.eq(0b1100),
                    data.eq(Cat(sink.data[:16], C(K29_7, 8), C(K23_7, 8))),
                    NextState("START"),
                ).Elif(~sink.be[3],
                    char_is_k.eq(0b1000),
                    data.eq(Cat(sink.data[:24], C(K29_7, 8))),
                    NextState("END-R-R"),
                ).Else(
                    char_is_k.eq(0b0000),
                    data.eq(sink.data),
                    NextState("END-T-R"),
                )
            ).Else(
                # Underflow: end the packet.
                sink.ready.eq(0),
                char_is_k.eq(0b0111),
                data.eq(Cat(C(K29_7, 8), C(K23_7, 8), C(K28_5, 8), C(D5_6, 8))),
                NextState("START"),
            )
        )
        fsm.act("END-T-R",
            # /T/R/ followed by /I/.
            char_is_k.eq(0b0111),
            data.eq(Cat(C(K29_7, 8), C(K23_7, 8), C(K28_5, 8), C(D5_6, 8))),
            NextState("START"),
        )
        fsm.act("END-R-R",
            # /R/R/ (completing /T/R/R/) followed by /I/.
            char_is_k.eq(0b0111),
            data.eq(Cat(C(K23_7, 8), C(K23_7, 8), C(K28_5, 8), C(D5_6, 8))),
            NextState("START"),
        )

# PCS RX 32-bit (Hard 8b/10b) ----------------------------------------------------------------------

class PCSRX32b(LiteXModule):
    """RX datapath for transceivers with a hard 8b/10b decoder (4 code-groups per cycle, lane 0
    first)."""
    def __init__(self):
        self.seen_valid_ci   = Signal()   # CI seen.
        self.seen_config_reg = Signal()   # Config seen.
        self.config_reg      = Signal(16) # Config register (16-bit).
        self.sgmii_speed     = Signal(2)  # Unused (no SGMII rate adaptation).
        self.source          = source = stream.Endpoint(eth_phy_description(32)) # Data output.

        # Transceiver interface.
        self.data          = Signal(32) # Code-groups (lane 0 first).
        self.char_is_k     = Signal(4)  # K-character flags.
        self.disparity_err = Signal(4)  # Disparity errors.
        self.table_err     = Signal(4)  # Not-in-table errors.

        # # #

        # Input Register.
        # ---------------
        # Eases routing from the transceiver.
        data      = Signal(32)
        char_is_k = Signal(4)
        error     = Signal(4)
        for s in [data, char_is_k, error]:
            s.attr.add("keep")
        self.sync += [
            data.eq(self.data),
            char_is_k.eq(self.char_is_k),
            error.eq(self.disparity_err | self.table_err),
        ]

        # Lane Alignment.
        # ---------------
        # The transceiver aligns commas to a byte lane, but /I/, /C/ and /S/ are not necessarily on
        # lane 0 and /C/ can straddle two words. Outside packets, realign the code-groups so that
        # the last comma or /S/ seen is on lane 0: ordered sets and packets then always start on
        # lane 0. The alignment is kept during packets so that no code-group is skipped.
        in_packet = Signal()
        prev_data      = Signal(32)
        prev_char_is_k = Signal(4)
        prev_error     = Signal(4)
        self.sync += [
            prev_data.eq(data),
            prev_char_is_k.eq(char_is_k),
            prev_error.eq(error),
        ]
        def is_special(d, k, e, lane):
            d = d[8*lane:8*(lane + 1)]
            return k[lane] & ~e[lane] & ((d == K28_5) | (d == K27_7))

        shift   = Signal(2)
        shift_r = Signal(2)
        self.comb += [
            shift.eq(shift_r),
            # Last special code-group of the previous word sets the shift.
            If(~in_packet,
                [If(is_special(prev_data, prev_char_is_k, prev_error, lane), shift.eq(lane))
                    for lane in range(4)],
            )
        ]
        self.sync += shift_r.eq(shift)

        w_data      = Signal(32) # Aligned code-groups.
        w_char_is_k = Signal(4)  # Aligned K-character flags.
        w_error     = Signal(4)  # Aligned errors.
        self.comb += Case(shift, {lane: [
            w_data.eq(Cat(prev_data, data)[8*lane:8*lane + 32]),
            w_char_is_k.eq(Cat(prev_char_is_k, char_is_k)[lane:lane + 4]),
            w_error.eq(Cat(prev_error, error)[lane:lane + 4]),
        ] for lane in range(4)})

        def lane_d(lane):
            return w_data[8*lane:8*(lane + 1)]
        def lane_is_d(lane):
            return ~w_char_is_k[lane] & ~w_error[lane]
        def lane_is_k(lane, k):
            return w_char_is_k[lane] & ~w_error[lane] & (lane_d(lane) == k)
        def lane_is_idle(lane):
            return (lane_is_k(lane, K28_5) & lane_is_d(lane + 1) &
                ((lane_d(lane + 1) == D5_6) | (lane_d(lane + 1) == D16_2)))

        # Buffer.
        # -------
        # Delay the received words by one cycle: a /T/ on lane 0 or an error marks the previous word
        # as the last one (with an error).
        abort = Signal()
        self.buffer = buffer = stream.Buffer(eth_phy_description(32),
            pipe_valid = True,
            pipe_ready = False,
        )
        self.comb += [
            buffer.source.connect(source, omit={"last", "error"}),
            source.last.eq(buffer.source.last | ~buffer.sink.valid),
            source.error.eq(buffer.source.error | Replicate(abort, 4)),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="START")
        self.comb += in_packet.eq(fsm.ongoing("DATA"))
        fsm.act("START",
            # Idle /I/ (on lane 0 or 2).
            If(lane_is_idle(0) | lane_is_idle(2),
                self.seen_valid_ci.eq(1),
            ),
            # Configuration /C/.
            If(lane_is_k(0, K28_5) & lane_is_d(1) & lane_is_d(2) & lane_is_d(3) &
               ((lane_d(1) == D21_5) | (lane_d(1) == D2_2)),
                self.seen_valid_ci.eq(1),
                self.seen_config_reg.eq(1),
                self.config_reg.eq(w_data[16:]),
            ),
            # Start-of-packet /S/ (replaced by the first preamble byte).
            If(lane_is_k(0, K27_7) & lane_is_d(1) & lane_is_d(2) & lane_is_d(3),
                buffer.sink.valid.eq(1),
                buffer.sink.be.eq(0b1111),
                buffer.sink.data.eq(Cat(C(0x55, 8), w_data[8:])),
                NextState("DATA"),
            )
        )
        fsm.act("DATA",
            If(lane_is_d(0) & lane_is_d(1) & lane_is_d(2) & lane_is_d(3),
                # Receive Data.
                buffer.sink.valid.eq(1),
                buffer.sink.be.eq(0b1111),
                buffer.sink.data.eq(w_data),
            ).Elif(lane_is_k(0, K29_7),
                # End-of-packet /T/ on lane 0: the previous word was the last one.
                NextState("START"),
            ).Elif(lane_is_d(0) & lane_is_k(1, K29_7),
                buffer.sink.valid.eq(1),
                buffer.sink.last.eq(1),
                buffer.sink.be.eq(0b0001),
                buffer.sink.data.eq(w_data),
                NextState("START"),
            ).Elif(lane_is_d(0) & lane_is_d(1) & lane_is_k(2, K29_7),
                buffer.sink.valid.eq(1),
                buffer.sink.last.eq(1),
                buffer.sink.be.eq(0b0011),
                buffer.sink.data.eq(w_data),
                NextState("START"),
            ).Elif(lane_is_d(0) & lane_is_d(1) & lane_is_d(2) & lane_is_k(3, K29_7),
                buffer.sink.valid.eq(1),
                buffer.sink.last.eq(1),
                buffer.sink.be.eq(0b0111),
                buffer.sink.data.eq(w_data),
                NextState("START"),
            ).Else(
                # Invalid code-group or unexpected K-character: end the packet with an error.
                abort.eq(1),
                NextState("START"),
            )
        )

# Compatibility aliases.
PCSTX = PCSTX8b
PCSRX = PCSRX8b

# PCS ----------------------------------------------------------------------------------------------

class PCS(LiteXModule):
    """1000BASE-X / SGMII PCS.

    sgmii selects the Auto-Negotiation flavour:
    - None  : detected from the link partner (bit 0 of its configuration word). The first
              configuration words sent are 1000BASE-X ones.
    - True  : SGMII MAC side from the start. Required by SGMII PHYs that only complete
              Auto-Negotiation with SGMII configuration words.
    - False : 1000BASE-X only.

    dw selects the datapath:
    - 8  : 8b/10b coder in the fabric, 10-bit TBI interface (tbi_tx/tbi_rx/tbi_rx_ce).
    - 32 : transceiver with a hard 8b/10b coder and a 4 code-group interface (tx.data/char_is_k,
           rx.data/char_is_k/disparity_err/table_err). 1000BASE-X only.

    sys_clk_freq sets the one-second CSR link-event debounce. If omitted, add_csr()
    uses the frequency from LiteXContext.top for compatibility with existing targets.
    """
    autocsr_exclude = {"ev"}
    def __init__(self, lsb_first=False,
        sgmii           = None,
        check_period    = 6e-3,
        breaklink_time  = 10e-3,
        more_ack_time   = 10e-3,
        sgmii_ack_time  = 1.6e-3,
        eth_tx_clk_freq = 125e6,
        with_csr        = False,
        with_autoneg    = True,
        sys_clk_freq    = None,
        dw              = 8,
    ):
        assert dw in [8, 32]
        self.sys_clk_freq = sys_clk_freq
        if dw == 8:
            self.tx = ClockDomainsRenamer("eth_tx")(PCSTX8b(lsb_first=lsb_first))
            self.rx = ClockDomainsRenamer("eth_rx")(PCSRX8b(lsb_first=lsb_first))

            self.tbi_tx    = self.tx.encoder.output[0]
            self.tbi_rx    = self.rx.decoder.input
            self.tbi_rx_ce = self.rx.decoder.ce
        else:
            # Hard 8b/10b coder: no SGMII rate adaptation.
            assert not sgmii
            sgmii   = False
            self.tx = ClockDomainsRenamer("eth_tx")(PCSTX32b())
            self.rx = ClockDomainsRenamer("eth_rx")(PCSRX32b())
        self.sink      = stream.Endpoint(eth_phy_description(dw))
        self.source    = stream.Endpoint(eth_phy_description(dw))
        if dw == 8:
            self.comb += self.source.be.eq(1)

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
                self.sink.connect(self.tx.sink,     omit={"be", "error"}),
                self.rx.source.connect(self.source, omit={"be"}),
            ]
        else:
            self.comb += [
                self.sink.connect(self.tx.sink, omit={"error"}),
                self.rx.source.connect(self.source),
            ]

        # Pulse Synchronizers.
        # --------------------
        self.seen_valid_ci      = seen_valid_ci      = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_abi  = rx_config_reg_abi  = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_ack  = rx_config_reg_ack  = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_zero = rx_config_reg_zero = PulseSynchronizer("eth_rx", "eth_tx")
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
        if sgmii is None and not with_autoneg:
            # If autoneg FSM is disabled, but sgmii mode is not specified, start in SGMII mode.
            sgmii = True
        sgmii_speed_valid = Signal()
        sgmii_tx_speed    = Signal(2)
        sgmii_rx_speed    = Signal(2)
        if sgmii is None:
            self.comb += is_sgmii.eq(self.lp_abi.o[0])
        else:
            self.comb += is_sgmii.eq(int(sgmii))
        self.comb += [
            # Speed 0b11 is reserved and an empty register means nothing received yet: 1000 Mbps.
            sgmii_speed_valid.eq((self.lp_abi.o[10:12] != 0b11) & (self.lp_abi.o != 0)),
            sgmii_tx_speed.eq(Mux(sgmii_speed_valid, self.lp_abi.o[10:12], SGMII_1000MBPS_SPEED)),
            sgmii_rx_speed.eq(Mux((self.lp_abi.i[10:12] != 0b11) & (self.lp_abi.i != 0),
                self.lp_abi.i[10:12], SGMII_1000MBPS_SPEED)),
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

        if with_autoneg:
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
                # Link partner restarted Auto-Negotiation: restart too.
                If(rx_config_reg_zero.o,
                    NextState("AUTONEG-BREAKLINK")
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
                # Link partner restarted Auto-Negotiation: restart too.
                If(rx_config_reg_zero.o,
                    NextState("AUTONEG-BREAKLINK")
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
                # Single-cycle pulses (to the PulseSynchronizers).
                rx_config_reg_abi.i.eq(0),
                rx_config_reg_ack.i.eq(0),
                rx_config_reg_zero.i.eq(0),
                If(self.rx.seen_config_reg,
                    # Consistency Count/Check.
                    rx_config_reg_last.eq(self.rx.config_reg),
                    If(self.rx.config_reg != rx_config_reg_last,
                        rx_config_reg_count.eq(8 - 1)
                    ).Else(
                        If(rx_config_reg_count != 0,
                            rx_config_reg_count.eq(rx_config_reg_count - 1),
                        ).Else(
                            # When RX Config is consistent (Clause 37).
                            If(self.rx.config_reg == 0,
                                # Link partner restarting Auto-Negotiation (AN_ENABLE/breaklink).
                                rx_config_reg_zero.i.eq(1),
                            ).Else(
                                # Ability match, regardless of the Acknowledge bit: the link
                                # partner can already be acknowledging.
                                rx_config_reg_abi.i.eq(1),
                                # Acknowledge match.
                                rx_config_reg_ack.i.eq(self.rx.config_reg[14]),
                            )
                        )
                    ),
                    self.lp_abi.i.eq(self.rx.config_reg)
                )
            ]
        else:
            # Fixed-rate link, no Clause 37 autonegotiation.
            self.comb += self.align.eq(1)
            self.sync.eth_tx += [
                # Release RX reset between failed checks so valid code groups can return.
                self.restart.eq(checker_tick & checker_error),
                If(checker_tick,
                    self.link_up.eq(~checker_error),
                ),
            ]

        if with_csr:
            self.add_csr()

    def add_csr(self, sys_clk_freq=None):
        if sys_clk_freq is None:
            sys_clk_freq = self.sys_clk_freq
        if sys_clk_freq is None:
            sys_clk_freq = getattr(LiteXContext.top, "sys_clk_freq", None)
        if sys_clk_freq is None or int(sys_clk_freq) <= 0:
            raise ValueError("A positive sys_clk_freq is required for PCS CSRs.")

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

        # The PCS status originates in eth_tx. Both software and the event FSM consume
        # synchronized system-domain copies; the link-partner word uses its bus handshake.
        link_up_tx  = Signal()
        is_sgmii_tx = Signal()
        self.sync.eth_tx += [
            link_up_tx.eq(self.link_up),
            is_sgmii_tx.eq(self.is_sgmii),
        ]
        self.specials += [
            MultiReg(link_up_tx,  self.status.fields.link_up),
            MultiReg(is_sgmii_tx, self.status.fields.is_sgmii),
        ]

        self.link_up_timer = link_up_timer = WaitTimer(int(sys_clk_freq))

        self.csr_fsm = fsm = FSM()
        fsm.act("DOWN",
            If(self.status.fields.link_up,
                NextState("UP")
            )
        )
        fsm.act("UP",
            link_up_timer.wait.eq(1),
            self.ev.link.trigger.eq(link_up_timer.done),
            If(~self.status.fields.link_up,
                NextState("DOWN"),
            )
        )
