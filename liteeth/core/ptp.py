#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""
LiteEth PTP (IEEE 1588v2) Slave Core — Layer 3 (UDP/IPv4) only.

Provides a PTP Slave implementation for LiteEth-based systems with TSU, Clock Servo,
Protocol FSM, and Monitoring. Currently supports Slave mode only (no Master/Boundary
Clock) and Layer 3 transport (UDP over IPv4) only (no Layer 2/Ethernet transport).

Features:
- End-To-End (E2E) and Peer-To-Peer (P2P) delay mechanisms.
- 48-bit seconds / 32-bit nanoseconds TSU with addend-based tick accumulation.
- Pipelined clock servo with phase correction and frequency trim.
- Outlier detection and seconds-boundary correction.
- Snapshot-based monitoring with common and debug CSR variants.
"""

from migen import *

from litex.gen import *
import math

from litex.gen.genlib.misc import WaitTimer

from litex.soc.interconnect.packet import Header, HeaderField
from litex.soc.interconnect.packet import Depacketizer, Packetizer
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from litex.soc.interconnect import stream

from liteeth.common import *

# PTP Constants ------------------------------------------------------------------------------------

PTP_EVENT_PORT          = 319
PTP_GENERAL_PORT        = 320

PTP_HEADER_LENGTH       = 34 # Bytes.

# Message types (low nibble).
PTP_MSG_SYNC            = 0x0  # Event
PTP_MSG_DELAY_REQ       = 0x1  # Event
PTP_MSG_PDELAY_REQ      = 0x2  # Event (P2P)
PTP_MSG_PDELAY_RESP     = 0x3  # Event (P2P)

PTP_MSG_FOLLOW_UP       = 0x8  # General
PTP_MSG_DELAY_RESP      = 0x9  # General
PTP_MSG_PDELAY_RESP_FUP = 0xA  # General (P2P)
PTP_MSG_ANNOUNCE        = 0xB  # General

PTP_VERSION             = 0x2
PTP_TWO_STEP_FLAG_BIT   = 9

# PTP Header ---------------------------------------------------------------------------------------

ptp_header_fields = {
    "msg_type"       : HeaderField(0,  0,  4),
    "transport_spec" : HeaderField(0,  4,  4),
    "version"        : HeaderField(1,  0,  4),
    "reserved0"      : HeaderField(1,  4,  4),
    "length"         : HeaderField(2,  0, 16),
    "domain_number"  : HeaderField(4,  0,  8),
    "reserved1"      : HeaderField(5,  0,  8),
    "flags"          : HeaderField(6,  0, 16),
    "correction"     : HeaderField(8,  0, 64),
    "reserved2"      : HeaderField(16, 0, 32),
    "source_port_id" : HeaderField(20, 0, 80),
    "sequence_id"    : HeaderField(30, 0, 16),
    "control_field"  : HeaderField(32, 0,  8),
    "log_interval"   : HeaderField(33, 0,  8),
}
ptp_header = Header(ptp_header_fields, PTP_HEADER_LENGTH, swap_field_bytes=True)

# PTP Description ----------------------------------------------------------------------------------


def ptp_description(dw):
    param_layout   = ptp_header.get_layout()
    payload_layout = [
        ("data",    dw),
        ("last_be", dw//8),
        ("error",   dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)

# LiteEthTSU ---------------------------------------------------------------------------------------


class LiteEthTSU(LiteXModule):
    """
    Time Stamping Unit (48-bit seconds, 32-bit nanoseconds).

    Addend-based tick accumulation with pipelined multiply and registered
    tick_inc to meet timing. Supports offset correction (±1s) and coarse
    step for initial lock.

    Parameters:
    - clk_freq : System clock frequency for default addend calculation.
    """
    def __init__(self, clk_freq):
        # Time Registers.
        # ---------------
        self.seconds     = Signal(48)
        self.nanoseconds = Signal(32)
        default_addend   = int(((1 << 32) + (clk_freq // 2)) // clk_freq)
        self.addend      = Signal(32, reset=default_addend)
        self.addend_frac = Signal(20)
        self.offset      = Signal((81, True))
        self.step        = Signal()
        self.step_target = Signal(80)

        # Timestamp Latches.
        # ------------------
        self.rx_ts     = Signal(80)
        self.tx_ts     = Signal(80)
        self.rx_latch  = Signal()
        self.tx_latch  = Signal()

        # # #

        # Tick Accumulation.
        # ------------------
        # Pipelined: addend → multiply | reg | frac add | reg | ns add.
        addend_frac_bits = len(self.addend_frac)
        full_addend_bits = len(self.addend) + addend_frac_bits
        full_addend      = Signal(full_addend_bits)
        inc_nsec_q       = Signal(full_addend_bits + 32)
        inc_nsec_q_r     = Signal(full_addend_bits + 32)
        frac             = Signal(32 + addend_frac_bits)
        frac_sum         = Signal(len(inc_nsec_q) + 1)
        tick_inc         = Signal(34)
        tick_inc_r       = Signal(34)
        tick_nsec        = Signal(34)
        self.comb += [
            full_addend.eq(Cat(self.addend_frac, self.addend)),
            inc_nsec_q.eq(full_addend * int(1_000_000_000)),
            frac_sum.eq(frac + inc_nsec_q_r),
            tick_inc.eq(frac_sum[32 + addend_frac_bits:32 + addend_frac_bits + len(tick_inc)]),
            tick_nsec.eq(self.nanoseconds + tick_inc_r),
        ]
        self.sync += [
            inc_nsec_q_r.eq(inc_nsec_q),
            tick_inc_r.eq(tick_inc),
        ]

        # Offset Correction.
        # ------------------
        offset_nsec = Signal((34, True))
        self.comb += offset_nsec.eq(self.nanoseconds + self.offset)

        # Time Update.
        # ------------
        self.sync += [
            # Coarse Step (initial lock).
            If(self.step,
                self.nanoseconds.eq(self.step_target[0:32]),
                self.seconds.eq(self.step_target[32:80]),
                frac.eq(0),
                self.offset.eq(0),
            # Offset Correction (phase/seconds adjust).
            ).Elif(self.offset != 0,
                If(offset_nsec < 0,
                    self.nanoseconds.eq(offset_nsec + 1_000_000_000),
                    self.seconds.eq(self.seconds - 1),
                ).Elif(offset_nsec >= 1_000_000_000,
                    self.nanoseconds.eq(offset_nsec - 1_000_000_000),
                    self.seconds.eq(self.seconds + 1),
                ).Else(
                    self.nanoseconds.eq(offset_nsec),
                ),
                self.offset.eq(0),
            # Normal Tick.
            ).Else(
                frac.eq(frac_sum[0:32 + addend_frac_bits]),
                If(tick_nsec >= 1_000_000_000,
                    self.nanoseconds.eq(tick_nsec - 1_000_000_000),
                    self.seconds.eq(self.seconds + 1),
                ).Else(
                    self.nanoseconds.eq(tick_nsec),
                ),
            ),
        ]

        # Timestamp Latch.
        # ----------------
        def pack80(sec, nsec):
            return Cat(nsec, sec)
        self.sync += [
            If(self.rx_latch, self.rx_ts.eq(pack80(self.seconds, self.nanoseconds))),
            If(self.tx_latch, self.tx_ts.eq(pack80(self.seconds, self.nanoseconds))),
        ]


# LiteEthPTPTX -------------------------------------------------------------------------------------

class LiteEthPTPTX(LiteXModule):
    """
    PTP TX (Delay_Req / Pdelay_Req Transmitter).

    Packetizes PTP header + 10-byte originTimestamp body. Asserts ``launch`` on
    the first accepted payload byte to trigger the TSU TX latch.
    """
    def __init__(self, tsu):
        # Control/Status.
        # ---------------
        self.start      = Signal()
        self.done       = Signal()
        self.launch     = Signal()

        # Parameters.
        # -----------
        self.seq_id     = Signal(16)
        self.domain     = Signal(8)
        self.clock_id   = Signal(80)
        self.msg_type   = Signal(4)
        self.ip_address = Signal(32)
        self.src_port   = Signal(16)
        self.dst_port   = Signal(16)
        self.p2p_mode   = Signal()

        # External UDP source (connect to UDP crossbar).
        # ----------------------------------------------
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # Packetizer.
        # -----------
        self.packetizer = packetizer = Packetizer(
            ptp_description(8),
            eth_udp_user_description(8),
            ptp_header
        )

        # # #

        # Signals.
        # --------
        pre_tx_ts  = Signal(80)
        self.count = count = Signal(4)
        ts_byte    = Signal(8)
        sec        = Signal(48)
        ns         = Signal(32)

        # Timestamp Formatting (Big-Endian).
        # ----------------------------------
        self.comb += [
            ns.eq( pre_tx_ts[ 0:32]),
            sec.eq(pre_tx_ts[32:80]),
        ]
        self.comb += Case(count, {
            0: ts_byte.eq(sec[40:48]),
            1: ts_byte.eq(sec[32:40]),
            2: ts_byte.eq(sec[24:32]),
            3: ts_byte.eq(sec[16:24]),
            4: ts_byte.eq(sec[ 8:16]),
            5: ts_byte.eq(sec[ 0: 8]),
            6: ts_byte.eq( ns[24:32]),
            7: ts_byte.eq( ns[16:24]),
            8: ts_byte.eq( ns[ 8:16]),
            9: ts_byte.eq( ns[ 0: 8]),
        })

        # Header.
        # -------
        self.comb += [
            # Fixed/Calculated Fields.
            packetizer.sink.transport_spec.eq(0),
            packetizer.sink.version.eq(PTP_VERSION),
            packetizer.sink.reserved0.eq(0),
            packetizer.sink.length.eq(PTP_HEADER_LENGTH + 10),
            packetizer.sink.reserved1.eq(0),
            packetizer.sink.flags.eq(0),
            packetizer.sink.correction.eq(0),
            packetizer.sink.reserved2.eq(0),
            packetizer.sink.control_field.eq(0x01),
            packetizer.sink.log_interval.eq(0),

            # Dynamic Parameters.
            packetizer.sink.msg_type.eq(self.msg_type),
            packetizer.sink.domain_number.eq(self.domain),
            packetizer.sink.source_port_id.eq(self.clock_id),
            packetizer.sink.sequence_id.eq(self.seq_id),

            # Payload Control.
            packetizer.sink.error.eq(0),
        ]

        # Pipeline.
        # ---------
        self.comb += packetizer.source.connect(source)
        self.comb += source.last_be.eq(1)

        # UDP Metadata.
        # -------------
        self.comb += [
            source.src_port.eq(self.src_port),
            source.dst_port.eq(self.dst_port),
            source.ip_address.eq(self.ip_address),
            source.length.eq(PTP_HEADER_LENGTH + 10),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self.done.eq(1),
            If(self.start,
                self.done.eq(0),
                NextValue(pre_tx_ts, Cat(tsu.nanoseconds, tsu.seconds)),
                NextValue(count, 0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.sink.valid.eq(1),
            packetizer.sink.data.eq(ts_byte),
            packetizer.sink.first.eq(count == 0),
            packetizer.sink.last.eq( count == 9),
            If(packetizer.sink.ready,
                NextValue(count, count + 1),
                self.launch.eq(packetizer.sink.first),
                If(packetizer.sink.last,
                    self.done.eq(1),
                    NextState("IDLE")
                )
            )
        )

# LiteEthPTPRX -------------------------------------------------------------------------------------

class LiteEthPTPRX(LiteXModule):
    """
    PTP RX (Depacketizer + Timestamp/Identity Extraction).

    Depacketizes UDP payload into PTP header + body. Extracts 10-byte timestamp and
    10-byte requestingPortIdentity. Uses WaitTimer + ResetInserter for deadlock protection.
    """
    TIMEOUT_CYCLES = 1024

    def __init__(self, udp_port, sys_clk_freq):
        # Control/Status.
        # ---------------
        self.present            = Signal(reset=0)
        self.error              = Signal()
        self.msg_type           = Signal(4)
        self.flags              = Signal(16)
        self.seq_id             = Signal(16)
        self.two_step           = Signal()
        self.timestamp          = Signal(80)
        self.requesting_port_id = Signal(80)
        self.domain             = Signal(8)
        self.invalid_header     = Signal()
        self.timeout_error      = Signal()

        # Depacketizer (with ResetInserter for timeout recovery).
        # -------------------------------------------------------
        rx_fsm_reset      = Signal()
        self.depacketizer = depacketizer = ResetInserter()(Depacketizer(
            eth_udp_user_description(8),
            ptp_description(8),
            ptp_header
        ))
        self.comb += depacketizer.reset.eq(rx_fsm_reset)
        self.sink = depacketizer.sink

        # # #

        # Signals.
        # --------
        bcount         = Signal(5)
        buf            = Array(Signal(8) for _ in range(20))
        version_ok     = Signal()
        domain_ok      = Signal()
        ts_type_raw    = Signal()
        supported_type = Signal()

        # Header Extraction.
        # ------------------
        self.comb += [
            self.msg_type.eq(depacketizer.source.msg_type),
            self.flags.eq(depacketizer.source.flags),
            self.seq_id.eq(depacketizer.source.sequence_id),
            self.two_step.eq(depacketizer.source.flags[PTP_TWO_STEP_FLAG_BIT]),
        ]

        # Validation.
        # -----------
        self.comb += [
            version_ok.eq(depacketizer.source.version     == PTP_VERSION),
            domain_ok.eq(depacketizer.source.domain_number == self.domain),
            ts_type_raw.eq(
                (self.msg_type == PTP_MSG_SYNC)          |
                (self.msg_type == PTP_MSG_FOLLOW_UP)     |
                (self.msg_type == PTP_MSG_DELAY_RESP)    |
                (self.msg_type == PTP_MSG_PDELAY_RESP)   |
                (self.msg_type == PTP_MSG_PDELAY_RESP_FUP)
            ),
            supported_type.eq(
                ts_type_raw |
                (self.msg_type == PTP_MSG_ANNOUNCE)
            )
        ]

        # Timeout.
        # --------
        self.timeout_timer = timeout_timer = WaitTimer(self.TIMEOUT_CYCLES)
        self.comb += rx_fsm_reset.eq(timeout_timer.done)

        # FSM.
        # ----
        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += [
            fsm.reset.eq(rx_fsm_reset),
            timeout_timer.wait.eq(~self.fsm.ongoing("IDLE")),
        ]

        fsm.act("IDLE",
            depacketizer.source.ready.eq(1),
            NextValue(self.present, 0),
            If(depacketizer.source.valid,
                If(version_ok & domain_ok & supported_type,
                    NextValue(buf[0], depacketizer.source.data),
                    If(depacketizer.source.last,
                        NextValue(bcount, 0),
                        NextState("END")
                    ).Else(
                        NextValue(bcount, 1),
                        NextState("BODY")
                    )
                ).Else(
                    NextState("SKIP")
                )
            )
        )

        fsm.act("BODY",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                If(bcount < 20,
                    NextValue(buf[bcount], depacketizer.source.data)
                ),
                If(depacketizer.source.last,
                    NextState("END")
                ).Else(
                    NextValue(bcount, bcount + 1)
                )
            )
        )

        fsm.act("SKIP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid & depacketizer.source.last,
                NextState("END")
            )
        )

        fsm.act("END",
            If(version_ok & domain_ok & supported_type,
                NextValue(self.timestamp, Cat(
                    buf[9], buf[8], buf[7], buf[6],
                    buf[5], buf[4], buf[3], buf[2], buf[1], buf[0]
                )),
                NextValue(self.requesting_port_id, Cat(
                    buf[19], buf[18], buf[17], buf[16], buf[15],
                    buf[14], buf[13], buf[12], buf[11], buf[10]
                )),
            ),
            NextState("DONE")
        )

        fsm.act("DONE",
            NextValue(self.present, version_ok & domain_ok & supported_type),
            NextState("IDLE")
        )

        # Error Reporting.
        # ----------------
        self.sync += [
            self.invalid_header.eq(0),
            self.timeout_error.eq(0),
            self.error.eq(0),
            If(self.fsm.ongoing("IDLE") & depacketizer.source.valid & ~(version_ok & domain_ok),
                self.invalid_header.eq(1),
            ),
            If(rx_fsm_reset,
                self.timeout_error.eq(1),
                self.error.eq(1),
            ),
        ]

# LiteEthPTPRxTimestamp ----------------------------------------------------------------------------

class LiteEthPTPRxTimestamp(LiteXModule):
    """RX Timestamp Latch Helper. Detects first beat of Event/General packets for TSU RX latch."""
    def __init__(self, event_port, general_port):
        self.event_first    = Signal()
        self.general_first  = Signal()
        self.event_msg_type = Signal(4)

        # # #

        event_in_pkt   = Signal()
        general_in_pkt = Signal()

        # Event Port.
        # -----------
        self.sync += [
            self.event_first.eq(0),
            If(event_port.source.valid & event_port.source.ready,
                If(~event_in_pkt,
                    self.event_first.eq(1),
                    self.event_msg_type.eq(event_port.source.data[0:4]),
                    event_in_pkt.eq(1)
                ),
                If(event_port.source.last,
                    event_in_pkt.eq(0)
                )
            )
        ]

        # General Port.
        # -------------
        self.sync += [
            self.general_first.eq(0),
            If(general_port.source.valid & general_port.source.ready,
                If(~general_in_pkt,
                    self.general_first.eq(1),
                    general_in_pkt.eq(1)
                ),
                If(general_port.source.last,
                    general_in_pkt.eq(0)
                )
            )
        ]

# LiteEthPTPClockServo -----------------------------------------------------------------------------

class LiteEthPTPClockServo(LiteXModule):
    """
    PTP Clock Servo (Pipelined).

    Computes phase offset and path delay from E2E/P2P timestamps, then
    applies phase correction (offset) and frequency trim (addend) to the
    TSU. Pipelined to meet timing constraints.

    Parameters:
    - tsu : LiteEthTSU instance.
    """
    def __init__(self, tsu):
        # Inputs.
        # -------
        self.t1       = Signal(80)
        self.t2       = Signal(80)
        self.t3       = Signal(80)
        self.t4       = Signal(80)
        self.p1       = Signal(80)
        self.p2       = Signal(80)
        self.p3       = Signal(80)
        self.p4       = Signal(80)
        self.p2p_mode = Signal()
        self.serve    = Signal()

        # Outputs (registered, coherent with serve_done).
        # -----------------------------------------------
        self.phase_error     = Signal((33, True))
        self.mean_path_delay = Signal((33, True))
        self.sample_valid    = Signal()
        self.dt21            = Signal((33, True))
        self.dt43            = Signal((33, True))

        # # #

        # Servo Parameters.
        # ------------------

        def compute_freq_shift(addend, frac_bits):
            """Compute the frequency integrator shift to ensure convergence.

            One addend LSB causes a clock drift of:
                lsb_drift = clk_freq * 1e9 / 2^(32 + frac_bits) ns/s
            The integrator divides the phase error by 2^freq_shift before
            adding to the addend. For stability, freq_shift must satisfy:
                2^freq_shift > lsb_drift / 2
            """
            clk_freq   = (1 << 32) / max(1, addend)
            lsb_drift  = clk_freq * 1e9 / (1 << (32 + frac_bits))
            return max(1, math.ceil(math.log2(max(1, lsb_drift))) + 1)

        addend_frac_bits = len(tsu.addend_frac)
        nominal_addend   = tsu.addend.reset.value
        one_billion      = int(1_000_000_000)

        # Phase servo gain (kp=1: full correction each exchange).
        kp = 1

        # Frequency integrator parameters.
        freq_shift       = compute_freq_shift(nominal_addend, addend_frac_bits)
        freq_deadband    = 256               # ns: ignore phase errors below this.
        freq_max_step    = 1 << max(0, addend_frac_bits - 3)  # Max step per serve.
        phase_clamp      = one_billion - 1   # Max phase correction (ns).

        # Addend clamping (nominal ± 1 integer unit).
        min_addend      = max(1, nominal_addend - 1)
        max_addend      = min((1 << 32) - 1, nominal_addend + 1)
        min_addend_full = min_addend << addend_frac_bits
        max_addend_full = max_addend << addend_frac_bits

        # Outlier detection thresholds.
        outlier_near_ns  = 50_000_000   # ±50ms window around second boundary.
        outlier_max_delay = 5_000_000   # Max plausible delay for an outlier.

        # Pipelined Servo (7 stages: serve → s1 → s2 → s3 → s4 → s5 → apply).
        full_addend_bits = len(tsu.addend) + addend_frac_bits

        # Shadow Addend.
        # ---------------
        # Authoritative addend copy maintained by the servo. The TSU register
        # is written from shadow (never read back) to prevent any external
        # perturbation from entering the feedback loop.
        shadow_addend       = Signal(full_addend_bits, reset=nominal_addend << addend_frac_bits)
        self._shadow_addend = shadow_addend

        # Pipeline valid shift register.
        # ------------------------------
        pipe_s1 = Signal()

        # Serve-done output: fires when the last pipeline stage commits.
        self.serve_done = Signal()

        # Debug Signals.
        # --------------
        exchange_outlier         = Signal()
        sec_adjust_needed        = Signal()
        sample_valid_now         = Signal()
        self._exchange_outlier   = exchange_outlier
        self._sec_adjust_needed  = sec_adjust_needed
        self._coarse_step_needed = Signal()

        # Helpers.
        # --------
        def signed_delta_ns(t_a_ns, t_a_sec, t_b_ns, t_b_sec):
            delta = Signal((33, True))
            self.comb += [
                If(t_a_sec == t_b_sec,
                    delta.eq(t_a_ns - t_b_ns)
                ).Elif(t_a_sec == (t_b_sec + 1),
                    delta.eq((t_a_ns + one_billion) - t_b_ns)
                ).Elif(t_b_sec == (t_a_sec + 1),
                    delta.eq(-((t_b_ns + one_billion) - t_a_ns))
                ).Else(
                    delta.eq(0)
                ),
            ]
            return delta

        def signed_half_toward_zero(value, result):
            return If(value < 0,
                result.eq((value + 1) >> 1)
            ).Else(
                result.eq(value >> 1)
            )

        def pipe_reg(pipe_in, assignments):
            """Create a pipeline register stage.

            Returns the next-stage valid signal. ``assignments`` is a list
            of ``(dest_signal, src_signal)`` pairs registered on ``pipe_in``.
            """
            pipe_out = Signal()
            self.sync += [
                pipe_out.eq(0),
                If(pipe_in, *[d.eq(s) for d, s in assignments],
                    pipe_out.eq(1),
                ),
            ]
            return pipe_out

        # Stage 0: Latch Inputs.
        # ----------------------
        # Capture all inputs into registered copies on serve pulse. The rest
        # of the pipeline only reads from these registers.
        s0_t1      = Signal(80)
        s0_t2      = Signal(80)
        s0_t3      = Signal(80)
        s0_t4      = Signal(80)
        s0_p1      = Signal(80)
        s0_p2      = Signal(80)
        s0_p3      = Signal(80)
        s0_p4      = Signal(80)
        s0_p2p     = Signal()
        s0_shadow  = Signal(full_addend_bits)
        s0_tsu_sec = Signal(48)
        s0_tsu_ns  = Signal(32)

        self.sync += [
            pipe_s1.eq(0),
            If(self.serve,
                s0_t1.eq(self.t1), s0_t2.eq(self.t2),
                s0_t3.eq(self.t3), s0_t4.eq(self.t4),
                s0_p1.eq(self.p1), s0_p2.eq(self.p2),
                s0_p3.eq(self.p3), s0_p4.eq(self.p4),
                s0_p2p.eq(self.p2p_mode),
                s0_shadow.eq(shadow_addend),
                s0_tsu_sec.eq(tsu.seconds),
                s0_tsu_ns.eq(tsu.nanoseconds),
                pipe_s1.eq(1),
            )
        ]

        # Timestamp extraction from stage 0 registers.
        # --------------------------------------------
        s0_t1_ns,  s0_t1_sec  = s0_t1[0:32], s0_t1[32:80]
        s0_t2_ns,  s0_t2_sec  = s0_t2[0:32], s0_t2[32:80]
        s0_t3_ns,  s0_t3_sec  = s0_t3[0:32], s0_t3[32:80]
        s0_t4_ns,  s0_t4_sec  = s0_t4[0:32], s0_t4[32:80]
        s0_p1_ns,  s0_p1_sec  = s0_p1[0:32], s0_p1[32:80]
        s0_p2_ns,  s0_p2_sec  = s0_p2[0:32], s0_p2[32:80]
        s0_p3_ns,  s0_p3_sec  = s0_p3[0:32], s0_p3[32:80]
        s0_p4_ns,  s0_p4_sec  = s0_p4[0:32], s0_p4[32:80]

        # Stage 1: Signed Deltas.
        # -----------------------
        # Compute signed time deltas between master/slave timestamps.
        # sample_valid_now checks time continuity (|Δsec| ≤ 1).
        t2_minus_t1        = Signal((33, True))
        t4_minus_t3        = Signal((33, True))
        p4_minus_p1        = Signal((33, True))
        p3_minus_p2        = Signal((33, True))
        c_sample_valid_now = Signal()

        self.comb += [
            t2_minus_t1.eq(signed_delta_ns(s0_t2_ns, s0_t2_sec, s0_t1_ns, s0_t1_sec)),
            t4_minus_t3.eq(signed_delta_ns(s0_t4_ns, s0_t4_sec, s0_t3_ns, s0_t3_sec)),
            p4_minus_p1.eq(signed_delta_ns(s0_p4_ns, s0_p4_sec, s0_p1_ns, s0_p1_sec)),
            p3_minus_p2.eq(signed_delta_ns(s0_p3_ns, s0_p3_sec, s0_p2_ns, s0_p2_sec)),
            c_sample_valid_now.eq(~(
                (s0_t1_sec > (s0_t2_sec + 1)) | (s0_t2_sec > (s0_t1_sec + 1))
            )),
        ]

        s1_t2_minus_t1 = Signal((33, True))
        s1_t4_minus_t3 = Signal((33, True))
        s1_p4_minus_p1 = Signal((33, True))
        s1_p3_minus_p2 = Signal((33, True))
        s1_valid_now   = Signal()
        s1_p2p         = Signal()
        s1_shadow      = Signal(full_addend_bits)
        s1_t1_ns       = Signal(32)
        s1_t1_sec      = Signal(48)
        s1_t2_ns       = Signal(32)
        s1_t2_sec      = Signal(48)
        s1_tsu_sec     = Signal(48)
        s1_tsu_ns      = Signal(32)

        pipe_s2 = pipe_reg(pipe_s1, [
            (s1_t2_minus_t1, t2_minus_t1),
            (s1_t4_minus_t3, t4_minus_t3),
            (s1_p4_minus_p1, p4_minus_p1),
            (s1_p3_minus_p2, p3_minus_p2),
            (s1_valid_now,   c_sample_valid_now),
            (s1_p2p,         s0_p2p),
            (s1_shadow,      s0_shadow),
            (s1_t1_ns,       s0_t1_ns),
            (s1_t1_sec,      s0_t1_sec),
            (s1_t2_ns,       s0_t2_ns),
            (s1_t2_sec,      s0_t2_sec),
            (s1_tsu_sec,     s0_tsu_sec),
            (s1_tsu_ns,      s0_tsu_ns),
        ])

        # Stage 2: Outlier Classification.
        # --------------------------------
        # Detect second-boundary artifacts: dt21 and dt43 both ≈ ±1s with
        # opposite signs and small delay. These are rejected from phase/freq
        # corrections but trigger a ±1s seconds adjustment instead.
        t2_minus_t1_abs    = Signal(33)
        t4_minus_t3_abs    = Signal(33)
        link_delay_e2e     = Signal((33, True))
        link_delay_e2e_sum = Signal((34, True))
        link_delay_e2e_abs = Signal(33)
        near_second_t21    = Signal()
        near_second_t43    = Signal()
        c_exchange_outlier = Signal()
        c_sec_adjust_dir   = Signal()

        self.comb += [
            t2_minus_t1_abs.eq(Mux(s1_t2_minus_t1 < 0, -s1_t2_minus_t1, s1_t2_minus_t1)),
            t4_minus_t3_abs.eq(Mux(s1_t4_minus_t3 < 0, -s1_t4_minus_t3, s1_t4_minus_t3)),
            link_delay_e2e_sum.eq(s1_t2_minus_t1 + s1_t4_minus_t3),
            signed_half_toward_zero(link_delay_e2e_sum, link_delay_e2e),
            link_delay_e2e_abs.eq(Mux(link_delay_e2e < 0, -link_delay_e2e, link_delay_e2e)),
            near_second_t21.eq(
                (t2_minus_t1_abs >= (one_billion - outlier_near_ns)) &
                (t2_minus_t1_abs <= (one_billion + outlier_near_ns))
            ),
            near_second_t43.eq(
                (t4_minus_t3_abs >= (one_billion - outlier_near_ns)) &
                (t4_minus_t3_abs <= (one_billion + outlier_near_ns))
            ),
            c_exchange_outlier.eq(
                (~s1_p2p) & near_second_t21 & near_second_t43 &
                ((s1_t2_minus_t1 < 0) != (s1_t4_minus_t3 < 0)) &
                (link_delay_e2e_abs <= outlier_max_delay)
            ),
            c_sec_adjust_dir.eq(s1_t2_minus_t1 > 0),
        ]

        s2_t2_minus_t1 = Signal((33, True))
        s2_t4_minus_t3 = Signal((33, True))
        s2_p4_minus_p1 = Signal((33, True))
        s2_p3_minus_p2 = Signal((33, True))
        s2_delay_e2e   = Signal((33, True))
        s2_outlier     = Signal()
        s2_sec_adj_dir = Signal()
        s2_valid_now   = Signal()
        s2_p2p         = Signal()
        s2_shadow      = Signal(full_addend_bits)
        s2_t1_ns       = Signal(32)
        s2_t1_sec      = Signal(48)
        s2_t2_ns       = Signal(32)
        s2_t2_sec      = Signal(48)
        s2_tsu_sec     = Signal(48)
        s2_tsu_ns      = Signal(32)

        pipe_s3 = pipe_reg(pipe_s2, [
            (s2_t2_minus_t1, s1_t2_minus_t1),
            (s2_t4_minus_t3, s1_t4_minus_t3),
            (s2_p4_minus_p1, s1_p4_minus_p1),
            (s2_p3_minus_p2, s1_p3_minus_p2),
            (s2_delay_e2e,   link_delay_e2e),
            (s2_outlier,     c_exchange_outlier),
            (s2_sec_adj_dir, c_sec_adjust_dir),
            (s2_valid_now,   s1_valid_now),
            (s2_p2p,         s1_p2p),
            (s2_shadow,      s1_shadow),
            (s2_t1_ns,       s1_t1_ns),
            (s2_t1_sec,      s1_t1_sec),
            (s2_t2_ns,       s1_t2_ns),
            (s2_t2_sec,      s1_t2_sec),
            (s2_tsu_sec,     s1_tsu_sec),
            (s2_tsu_ns,      s1_tsu_ns),
        ])

        # Stage 3: Phase/Delay/Coarse Classification.
        # -------------------------------------------
        # E2E: phase = (t2-t1 - t4+t3) / 2, delay = (t2-t1 + t4-t3) / 2.
        # P2P: phase = (t2-t1) - delay_p2p.
        # Coarse step fires for initial lock (|Δsec| > 1 or |phase| > 500ms).
        link_delay_p2p     = Signal((33, True))
        link_delay_p2p_sum = Signal((34, True))
        offset_e2e         = Signal((33, True))
        offset_e2e_sum     = Signal((34, True))
        offset_p2p         = Signal((33, True))
        err_phase          = Signal((33, True))
        err_phase_abs      = Signal(33)
        coarse_step_needed = Signal()

        self.comb += [
            link_delay_p2p_sum.eq(s2_p4_minus_p1 - s2_p3_minus_p2),
            signed_half_toward_zero(link_delay_p2p_sum, link_delay_p2p),
            offset_e2e_sum.eq(s2_t2_minus_t1 - s2_t4_minus_t3),
            signed_half_toward_zero(offset_e2e_sum, offset_e2e),
            offset_p2p.eq(s2_t2_minus_t1 - link_delay_p2p),
            err_phase.eq(Mux(s2_p2p, offset_p2p, offset_e2e)),
            err_phase_abs.eq(Mux(err_phase < 0, -err_phase, err_phase)),
            coarse_step_needed.eq(
                (s2_t2_sec > (s2_t1_sec + 1)) |
                (s2_t1_sec > (s2_t2_sec + 1)) |
                (err_phase_abs >= (one_billion // 2))
            ),
        ]

        s3_err_phase     = Signal((33, True))
        s3_coarse_needed = Signal()
        s3_outlier       = Signal()
        s3_sec_adj_dir   = Signal()
        s3_valid_now     = Signal()
        s3_p2p           = Signal()
        s3_delay_p2p     = Signal((33, True))
        s3_delay_e2e     = Signal((33, True))
        s3_t2_minus_t1   = Signal((33, True))
        s3_t4_minus_t3   = Signal((33, True))
        s3_shadow        = Signal(full_addend_bits)
        s3_t1_ns         = Signal(32)
        s3_t1_sec        = Signal(48)
        s3_t2_ns         = Signal(32)
        s3_t2_sec        = Signal(48)
        s3_tsu_sec       = Signal(48)
        s3_tsu_ns        = Signal(32)

        pipe_s4 = pipe_reg(pipe_s3, [
            (s3_err_phase,     err_phase),
            (s3_coarse_needed, coarse_step_needed),
            (s3_outlier,       s2_outlier),
            (s3_sec_adj_dir,   s2_sec_adj_dir),
            (s3_valid_now,     s2_valid_now),
            (s3_p2p,           s2_p2p),
            (s3_delay_p2p,     link_delay_p2p),
            (s3_delay_e2e,     s2_delay_e2e),
            (s3_t2_minus_t1,   s2_t2_minus_t1),
            (s3_t4_minus_t3,   s2_t4_minus_t3),
            (s3_shadow,        s2_shadow),
            (s3_t1_ns,         s2_t1_ns),
            (s3_t1_sec,        s2_t1_sec),
            (s3_t2_ns,         s2_t2_ns),
            (s3_t2_sec,        s2_t2_sec),
            (s3_tsu_sec,       s2_tsu_sec),
            (s3_tsu_ns,        s2_tsu_ns),
        ])

        # Stage 4: Freq Step + Phase Correction + Elapsed Time.
        # -----------------------------------------------------
        # Frequency integrator: err_freq → deadband → shift → clamp → step.
        # Phase correction: -err_phase * kp, clamped to ±(1e9-1).
        # Elapsed time: first half of coarse target (split for timing).
        err_freq         = Signal((33, True))
        err_freq_ext     = Signal((64, True))
        err_freq_abs     = Signal(64)
        freq_step_mag    = Signal(64)
        freq_step        = Signal((64, True))
        phase_correction = Signal((81, True))
        elapsed_sec      = Signal(48)
        elapsed_ns       = Signal(32)

        self.comb += [
            err_freq.eq(-s3_err_phase),
            err_freq_ext.eq(err_freq),
            err_freq_abs.eq(Mux(err_freq_ext < 0, -err_freq_ext, err_freq_ext)),
            freq_step_mag.eq(err_freq_abs >> freq_shift),
            If(err_freq_abs <= freq_deadband,
                freq_step.eq(0)
            ).Elif(freq_step_mag > freq_max_step,
                freq_step.eq(Mux(err_freq_ext < 0, -freq_max_step, freq_max_step))
            ).Else(
                freq_step.eq(Mux(err_freq_ext < 0, -freq_step_mag, freq_step_mag))
            ),
            If(((-s3_err_phase) * kp) > phase_clamp,
                phase_correction.eq(phase_clamp)
            ).Elif(((-s3_err_phase) * kp) < -phase_clamp,
                phase_correction.eq(-phase_clamp)
            ).Else(
                phase_correction.eq((-s3_err_phase) * kp)
            ),
            If(s3_tsu_ns >= s3_t2_ns,
                elapsed_ns.eq(s3_tsu_ns - s3_t2_ns),
                elapsed_sec.eq(s3_tsu_sec - s3_t2_sec),
            ).Else(
                elapsed_ns.eq(s3_tsu_ns + one_billion - s3_t2_ns),
                elapsed_sec.eq(s3_tsu_sec - s3_t2_sec - 1),
            ),
        ]

        s4_freq_step     = Signal((64, True))
        s4_phase_corr    = Signal((81, True))
        s4_coarse_needed = Signal()
        s4_elapsed_ns    = Signal(32)
        s4_elapsed_sec   = Signal(48)
        s4_t1_ns         = Signal(32)
        s4_t1_sec        = Signal(48)
        s4_outlier       = Signal()
        s4_sec_adj_dir   = Signal()
        s4_valid_now     = Signal()
        s4_p2p           = Signal()
        s4_delay_p2p     = Signal((33, True))
        s4_delay_e2e     = Signal((33, True))
        s4_t2_minus_t1   = Signal((33, True))
        s4_t4_minus_t3   = Signal((33, True))
        s4_err_phase     = Signal((33, True))
        s4_shadow        = Signal(full_addend_bits)

        pipe_s5 = pipe_reg(pipe_s4, [
            (s4_freq_step,     freq_step),
            (s4_phase_corr,    phase_correction),
            (s4_coarse_needed, s3_coarse_needed),
            (s4_elapsed_ns,    elapsed_ns),
            (s4_elapsed_sec,   elapsed_sec),
            (s4_t1_ns,         s3_t1_ns),
            (s4_t1_sec,        s3_t1_sec),
            (s4_outlier,       s3_outlier),
            (s4_sec_adj_dir,   s3_sec_adj_dir),
            (s4_valid_now,     s3_valid_now),
            (s4_p2p,           s3_p2p),
            (s4_delay_p2p,     s3_delay_p2p),
            (s4_delay_e2e,     s3_delay_e2e),
            (s4_t2_minus_t1,   s3_t2_minus_t1),
            (s4_t4_minus_t3,   s3_t4_minus_t3),
            (s4_err_phase,     s3_err_phase),
            (s4_shadow,        s3_shadow),
        ])

        # Stage 5: Addend Update + Coarse Target.
        # ----------------------------------------
        # addend_next = shadow + freq_step, clamped to nominal ± 1.
        # Coarse target: t1 + elapsed time since t2.
        addend_u          = Signal(full_addend_bits)
        addend_s          = Signal((full_addend_bits + 1, True))
        freq_sum          = Signal((65, True))
        addend_next       = Signal(full_addend_bits)
        coarse_target_sec = Signal(48)
        coarse_target_ns  = Signal(32)

        self.comb += [
            addend_u.eq(s4_shadow),
            addend_s.eq(addend_u),
            freq_sum.eq(addend_s + s4_freq_step),
            If(freq_sum < min_addend_full,
                addend_next.eq(Cat(freq_sum[:addend_frac_bits], min_addend))
            ).Elif(freq_sum > max_addend_full,
                addend_next.eq(Cat(freq_sum[:addend_frac_bits], max_addend))
            ).Else(
                addend_next.eq(freq_sum[:full_addend_bits])
            ),
            If((s4_t1_ns + s4_elapsed_ns) >= one_billion,
                coarse_target_ns.eq(s4_t1_ns + s4_elapsed_ns - one_billion),
                coarse_target_sec.eq(s4_t1_sec + s4_elapsed_sec + 1),
            ).Else(
                coarse_target_ns.eq(s4_t1_ns + s4_elapsed_ns),
                coarse_target_sec.eq(s4_t1_sec + s4_elapsed_sec),
            ),
        ]

        s5_addend_next    = Signal(full_addend_bits)
        s5_phase_corr     = Signal((81, True))
        s5_coarse_needed  = Signal()
        s5_coarse_tgt_ns  = Signal(32)
        s5_coarse_tgt_sec = Signal(48)
        s5_outlier        = Signal()
        s5_sec_adj_dir    = Signal()
        s5_valid_now      = Signal()
        s5_p2p            = Signal()
        s5_delay_p2p      = Signal((33, True))
        s5_delay_e2e      = Signal((33, True))
        s5_t2_minus_t1    = Signal((33, True))
        s5_t4_minus_t3    = Signal((33, True))
        s5_err_phase      = Signal((33, True))
        s5_freq_step      = Signal((64, True))

        pipe_s6 = pipe_reg(pipe_s5, [
            (s5_addend_next,   addend_next),
            (s5_phase_corr,    s4_phase_corr),
            (s5_coarse_needed, s4_coarse_needed),
            (s5_coarse_tgt_ns, coarse_target_ns),
            (s5_coarse_tgt_sec, coarse_target_sec),
            (s5_outlier,       s4_outlier),
            (s5_sec_adj_dir,   s4_sec_adj_dir),
            (s5_valid_now,     s4_valid_now),
            (s5_p2p,           s4_p2p),
            (s5_delay_p2p,     s4_delay_p2p),
            (s5_delay_e2e,     s4_delay_e2e),
            (s5_t2_minus_t1,   s4_t2_minus_t1),
            (s5_t4_minus_t3,   s4_t4_minus_t3),
            (s5_err_phase,     s4_err_phase),
            (s5_freq_step,     s4_freq_step),
        ])

        # Expose registered diagnostics.
        self._addend_next = s5_addend_next
        self._freq_step   = s5_freq_step
        self.comb += self._coarse_step_needed.eq(s5_coarse_needed)

        # Stage 6: Apply to TSU.
        # ----------------------
        # Three outcomes:
        # - Good exchange:  apply phase correction + addend update.
        # - Outlier:        apply ±1s seconds correction via offset.
        # - Coarse:         jump TSU to master time (initial lock).
        # When idle, continuously restore TSU addend from shadow to
        # maintain coherence (shadow is the authoritative copy).
        good_serve = Signal()
        self.comb += good_serve.eq(
            pipe_s6 & s5_valid_now & ~s5_coarse_needed & ~s5_outlier
        )

        self.sync += [
            self.serve_done.eq(0),
            If(good_serve,
                tsu.offset.eq(s5_phase_corr),
                shadow_addend.eq(s5_addend_next),
                tsu.addend.eq(s5_addend_next[addend_frac_bits:addend_frac_bits + len(tsu.addend)]),
                tsu.addend_frac.eq(s5_addend_next[:addend_frac_bits]),
                self.serve_done.eq(1),
            ).Elif(pipe_s6 & s5_outlier,
                If(s5_sec_adj_dir,
                    tsu.offset.eq(-one_billion),
                ).Else(
                    tsu.offset.eq(one_billion),
                ),
                self.serve_done.eq(1),
            ).Elif(pipe_s6 & s5_coarse_needed & ~s5_outlier,
                tsu.step.eq(1),
                tsu.step_target.eq(Cat(s5_coarse_tgt_ns, s5_coarse_tgt_sec)),
                self.serve_done.eq(1),
            ).Else(
                tsu.step.eq(0),

                tsu.addend.eq(shadow_addend[addend_frac_bits:addend_frac_bits + len(tsu.addend)]),
                tsu.addend_frac.eq(shadow_addend[:addend_frac_bits]),
            )
        ]


        self.sync += [
            If(pipe_s6,
                exchange_outlier.eq(s5_outlier),
                sec_adjust_needed.eq(s5_outlier),
                sample_valid_now.eq(s5_valid_now),
                self.phase_error.eq(s5_err_phase),
                self.mean_path_delay.eq(Mux(s5_p2p, s5_delay_p2p, s5_delay_e2e)),
                self.sample_valid.eq(~s5_outlier & s5_valid_now),
                self.dt21.eq(s5_t2_minus_t1),
                self.dt43.eq(s5_t4_minus_t3),
            )
        ]

# LiteEthPTPControl --------------------------------------------------------------------------------

class LiteEthPTPControl(LiteXModule):
    """
    PTP Protocol FSM.

    Manages the E2E/P2P exchange sequence: WAIT_SYNC → WAIT_FUP → SEND_DELAY_REQ →
    WAIT_DELAY_RESP → SERVE → LOCKED, and drives TX, Servo, and timestamp latching.
    """
    def __init__(self, tsu, tx, rx_ev, rx_ge, servo, latcher, event_source, general_source,
        enable=Signal(reset=1), require_announce=Signal(reset=0), announce_timeout_cycles=None):
        # Control/Status.
        # ---------------
        self.locked             = Signal() # o
        self.peer_mismatch      = Signal() # o
        self.requester_mismatch = Signal() # o
        self.announce_expired   = Signal() # o

        # Master IP Storage.
        # ------------------
        self.master_ip = Signal(32) # o

        # # #

        # Signals.
        # --------
        seq                   = Signal(16) # PTP Sequence ID.
        rx_ts_shadow          = Signal(80)
        tx_ts_shadow          = Signal(80)
        rx_ts_shadow_valid    = Signal()
        tx_ts_shadow_valid    = Signal()
        skip_stale_sync       = Signal()
        rx_ts_capture_pending = Signal()
        tx_ts_capture_pending = Signal()
        t1                    = Signal(80)
        t2                    = Signal(80)
        t3                    = Signal(80)
        t4                    = Signal(80)
        p1                    = Signal(80)
        p2                    = Signal(80)
        p3                    = Signal(80)
        p4                    = Signal(80)
        have_t1               = Signal()
        have_t2               = Signal()
        have_t3               = Signal()
        have_t4               = Signal()
        master_known          = Signal()
        announce_seen         = Signal()
        announce_expired      = Signal()
        event_from_master     = Signal()
        general_from_master   = Signal()
        event_msg_type        = Signal(4)
        event_is_sync         = Signal()
        event_is_pdelay_resp  = Signal()
        event_is_latchable    = Signal()

        if hasattr(latcher, "event_msg_type"):
            self.comb += event_msg_type.eq(latcher.event_msg_type)
            self.comb += [
                event_is_sync.eq(event_msg_type == PTP_MSG_SYNC),
                event_is_pdelay_resp.eq(event_msg_type == PTP_MSG_PDELAY_RESP),
            ]
        else:
            self.comb += [
                event_is_sync.eq(1),
                event_is_pdelay_resp.eq(1),
            ]

        self.comb += [
            master_known.eq(self.master_ip != 0),
            event_from_master.eq(event_source.ip_address == self.master_ip),
            general_from_master.eq(general_source.ip_address == self.master_ip),
            event_is_latchable.eq(event_is_sync | (tx.p2p_mode & event_is_pdelay_resp)),
        ]

        # FSM.
        # ----
        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += fsm.reset.eq((~enable) | announce_expired)
        self.comb += announce_seen.eq(
            fsm.ongoing("WAIT_SYNC") &
            rx_ge.present &
            (rx_ge.msg_type == PTP_MSG_ANNOUNCE)
        )
        self.sync += self.announce_expired.eq(0)

        # FSM -> TX.
        self.comb += [
            tx.start.eq(0),
            tx.seq_id.eq(seq),
        ]


        # IDLE: wait for enable, clear all state.
        fsm.act("IDLE",
            If(enable,
                NextValue(self.locked, 0),
                NextValue(have_t1, 0),
                NextValue(have_t2, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_SYNC")
            )
        )

        # WAIT_SYNC: accept Announce (learn master IP), then wait for Sync.
        # If a Sync arrives from the master, latch t2 (RX timestamp) and
        # proceed to Follow_Up (two-step) or Delay_Req (one-step).
        fsm.act("WAIT_SYNC",
            If(rx_ge.present & (rx_ge.msg_type == PTP_MSG_ANNOUNCE),
                If((~master_known) | (~general_from_master),
                    NextValue(self.locked, 0)
                ),
                NextValue(self.master_ip, general_source.ip_address)
            ),
            # Wait for SYNC message on Event port.
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               (Mux(require_announce,
                    master_known & event_from_master,
                    (~master_known) | event_from_master)),
                If(skip_stale_sync,
                    # Skip this Sync — it was queued during the previous exchange.
                    NextValue(skip_stale_sync, 0)
                ).Else(
                    NextValue(seq, rx_ev.seq_id),
                    If(rx_ts_shadow_valid,
                        NextValue(t2, rx_ts_shadow),
                        NextValue(have_t2, 1)
                    ).Else(
                        NextValue(have_t2, 0)
                    ),
                    NextValue(rx_ts_shadow_valid, 0),
                    NextValue(have_t3, 0),
                    NextValue(have_t4, 0),
                    If(rx_ev.two_step,
                        NextValue(have_t1, 0),
                        NextState("WAIT_FUP")
                    ).Else(
                        NextValue(t1, rx_ev.timestamp),
                        NextValue(have_t1, 1),
                        If(tx.p2p_mode,
                            NextState("SEND_PDELAY_REQ")
                        ).Else(
                            NextState("SEND_DELAY_REQ")
                        )
                    )
                )
            ),
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               master_known & ~event_from_master,
                self.peer_mismatch.eq(1)
            ),
        )

        # WAIT_FUP: wait for Follow_Up to get t1 (master origin timestamp).
        fsm.act("WAIT_FUP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_FOLLOW_UP) &
               (rx_ge.seq_id  == seq) &
               ~general_from_master,
                self.peer_mismatch.eq(1)
            ),
            # Wait for FOLLOW_UP message on General port (matching sequence ID).
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_FOLLOW_UP) &
               (rx_ge.seq_id  == seq) &
               general_from_master,
                NextValue(t1, rx_ge.timestamp),
                NextValue(have_t1, 1),
                If(tx.p2p_mode,
                    NextState("SEND_PDELAY_REQ")
                ).Else(
                    NextState("SEND_DELAY_REQ")
                )
            )
        )

        # E2E path: Delay_Req / Delay_Resp.
        fsm.act("SEND_DELAY_REQ",
            # Send Delay_Req. TX module sets PTP_MSG_DELAY_REQ.
            tx.start.eq(1),
            If(tx.done,
                If(tx_ts_shadow_valid,
                    NextValue(t3, tx_ts_shadow),
                    NextValue(have_t3, 1)
                ).Else(
                    NextValue(have_t3, 0)
                ),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_DELAY_RESP")
            )
        )
        # WAIT_DELAY_RESP: wait for Delay_Resp from master to get t4.
        # Also accept a new Sync if one arrives (restart the exchange).
        fsm.act("WAIT_DELAY_RESP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_DELAY_RESP) &
               (rx_ge.seq_id  == seq) &
               ~general_from_master,
                self.peer_mismatch.eq(1)
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_DELAY_RESP) &
               (rx_ge.seq_id  == seq) &
               general_from_master &
               (rx_ge.requesting_port_id != tx.clock_id),
                self.requester_mismatch.eq(1)
            ),
            # Wait for Delay_Resp on General port (matching sequence ID).
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_DELAY_RESP) &
               (rx_ge.seq_id  == seq) &
               general_from_master &
               (rx_ge.requesting_port_id == tx.clock_id),
                NextValue(t4, rx_ge.timestamp),
                NextValue(have_t4, 1),
                NextState("SERVE")
            )
        )

        # P2P path: Pdelay_Req / Pdelay_Resp.
        fsm.act("SEND_PDELAY_REQ",
            # Send Pdelay_Req. TX module sets PTP_MSG_PDELAY_REQ.
            tx.start.eq(1),
            If(tx.done,
                If(tx_ts_shadow_valid,
                    NextValue(p1, tx_ts_shadow)
                ),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_PDELAY_RESP")
            )
        )
        # WAIT_PDELAY_RESP: wait for Pdelay_Resp to get p2 and p4.
        fsm.act("WAIT_PDELAY_RESP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_PDELAY_RESP) &
               (rx_ev.seq_id  == seq) &
               ~event_from_master,
                self.peer_mismatch.eq(1)
            ),
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_PDELAY_RESP) &
               (rx_ev.seq_id  == seq) &
               event_from_master &
               (rx_ev.requesting_port_id != tx.clock_id),
                self.requester_mismatch.eq(1)
            ),
            # Wait for Pdelay_Resp on Event port (matching sequence ID).
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_PDELAY_RESP) &
               (rx_ev.seq_id  == seq) &
               event_from_master &
               (rx_ev.requesting_port_id == tx.clock_id),
                NextValue(p2, rx_ev.timestamp),
                If(rx_ts_shadow_valid,
                    NextValue(p4, rx_ts_shadow)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                If(rx_ev.two_step,
                    NextState("WAIT_PDELAY_RESP_FUP")
                ).Else(
                    NextValue(p3, rx_ev.timestamp),
                    NextState("SERVE")
                )
            )
        )
        # WAIT_PDELAY_RESP_FUP: wait for Pdelay_Resp_Follow_Up to get p3.
        fsm.act("WAIT_PDELAY_RESP_FUP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_PDELAY_RESP_FUP) &
               (rx_ge.seq_id  == seq) &
               ~general_from_master,
                self.peer_mismatch.eq(1)
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_PDELAY_RESP_FUP) &
               (rx_ge.seq_id  == seq) &
               general_from_master,
                NextValue(p3, rx_ge.timestamp),
                NextState("SERVE")
            )
        )

        # SERVE: trigger servo pipeline. Wait for all 4 timestamps in E2E.
        fsm.act("SERVE",
            If(tx.p2p_mode | (have_t1 & have_t2 & have_t3 & have_t4),
                NextState("LOCKED")
            ).Else(
                NextValue(self.locked, 0),
                NextState("WAIT_SYNC")
            )
        )

        # LOCKED: mark locked, skip stale Sync if last exchange was good
        # (a Sync may have been queued in the depacketizer during SERVE).
        fsm.act("LOCKED",
            NextValue(skip_stale_sync, servo.sample_valid),
            NextValue(rx_ts_shadow_valid, 0),
            NextValue(rx_ts_capture_pending, 0),
            NextValue(self.locked, 1),
            NextState("WAIT_SYNC")
        )


        # FSM -> Servo.
        # -------------
        # Connect timestamp registers and trigger servo pipeline one cycle
        # before LOCKED (fsm.before_entering fires in the SERVE state).
        self.comb += [
            servo.t1.eq(t1),
            servo.t2.eq(t2),
            servo.t3.eq(t3),
            servo.t4.eq(t4),
            servo.p1.eq(p1),
            servo.p2.eq(p2),
            servo.p3.eq(p3),
            servo.p4.eq(p4),
            servo.p2p_mode.eq(tx.p2p_mode),
            servo.serve.eq(fsm.before_entering("LOCKED")),
        ]

        # TSU Latch Drivers.
        # ------------------
        # The TSU's TX and RX latches are driven by the FSM's state and the latcher outputs.
        self.comb += [
            tsu.tx_latch.eq(tx.launch),
            tsu.rx_latch.eq(
                (fsm.ongoing("WAIT_SYNC")            |
                 fsm.ongoing("WAIT_FUP")             |
                 fsm.ongoing("WAIT_DELAY_RESP")      |
                 fsm.ongoing("WAIT_PDELAY_RESP")     |
                 fsm.ongoing("WAIT_PDELAY_RESP_FUP")) &
                latcher.event_first &
                event_is_latchable
            ),
        ]

        if hasattr(tsu, "seconds") and hasattr(tsu, "nanoseconds"):
            self.sync += [
                If(tsu.rx_latch,
                    rx_ts_capture_pending.eq(1)
                ).Elif(rx_ts_capture_pending,
                    rx_ts_shadow.eq(tsu.rx_ts),
                    rx_ts_shadow_valid.eq(1),
                    rx_ts_capture_pending.eq(0)
                ),
                If(tsu.tx_latch,
                    tx_ts_capture_pending.eq(1)
                ).Elif(tx_ts_capture_pending,
                    tx_ts_shadow.eq(tsu.tx_ts),
                    tx_ts_shadow_valid.eq(1),
                    tx_ts_capture_pending.eq(0)
                ),
            ]
        else:
            self.comb += [
                rx_ts_shadow.eq(tsu.rx_ts),
                tx_ts_shadow.eq(tsu.tx_ts),
                rx_ts_shadow_valid.eq(1),
                tx_ts_shadow_valid.eq(1),
            ]

        # Master IP Latching.
        # -------------------
        self.sync += [
            If(announce_expired,
                self.master_ip.eq(0),
                self.locked.eq(0)
            ),
            If(fsm.ongoing("WAIT_SYNC") &
               rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               (Mux(require_announce,
                    master_known & event_from_master,
                    (~master_known) | event_from_master)),
                # SYNC is on the Event Port
                self.master_ip.eq(event_source.ip_address)
            ).Elif(fsm.ongoing("WAIT_SYNC") &
               rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_ANNOUNCE),
                self.master_ip.eq(general_source.ip_address)
            ).Elif(fsm.ongoing("WAIT_FUP") & rx_ge.present & (rx_ge.msg_type == PTP_MSG_FOLLOW_UP),
                # FOLLOW_UP is on the General Port
                self.master_ip.eq(general_source.ip_address)
            )
        ]

        if announce_timeout_cycles is not None:
            announce_counter = Signal(max=max(2, announce_timeout_cycles + 1))
            self.sync += [
                announce_expired.eq(0),
                If((~enable) | announce_expired,
                    announce_counter.eq(0)
                ).Elif(announce_seen,
                    announce_counter.eq(announce_timeout_cycles)
                ).Elif(master_known & (announce_counter != 0),
                    announce_counter.eq(announce_counter - 1),
                    If(announce_counter == 1,
                        self.announce_expired.eq(1),
                        announce_expired.eq(1)
                    )
                )
            ]
        else:
            self.sync += [
                announce_expired.eq(0),
            ]
# LiteEthPTP ---------------------------------------------------------------------------------------

class LiteEthPTP(LiteXModule):
    """
    PTP Top-Level Module.

    Integrates TSU, TX/RX, Protocol FSM, and Clock Servo to provide a
    complete PTP slave implementation. Supports E2E and P2P delay mechanisms.

    An optional snapshot-based Monitor (LiteEthPTPMonitor) is added by
    default via ``add_monitor()``. Pass ``monitor_debug=None`` to disable.

    Parameters:
    - event_port    : UDP port for PTP event messages (port 319).
    - general_port  : UDP port for PTP general messages (port 320).
    - sys_clk_freq  : System clock frequency.
    - timeout       : Lock timeout in seconds.
    - monitor_debug : True=full, False=common-only, None=no monitor.
    """
    def __init__(self, event_port, general_port, sys_clk_freq, timeout=1.0, announce_timeout=None,
        require_announce=False, monitor_debug=False):
        # Control/Status.
        # ---------------
        self.enable                = Signal(reset=1)
        self.locked                = Signal()
        self.timeout               = Signal()
        self.p2p_mode              = Signal(reset=0)
        self.require_announce      = Signal(reset=1 if require_announce else 0)
        self.master_ip             = Signal(32)
        self.last_rx_msg_type      = Signal(4)
        self.last_rx_seq_id        = Signal(16)
        self.invalid_header_count  = Signal(32)
        self.wrong_peer_count      = Signal(32)
        self.wrong_requester_count = Signal(32)
        self.rx_timeout_count      = Signal(32)
        self.announce_expiry_count = Signal(32)

        # Parameters.
        # -----------
        self.clock_id = Signal(80, reset=0x0000000000000001)
        self.domain   = Signal(8,  reset=0)

        # # #

        # 1. Time-Stamping Unit (TSU).
        self.tsu = tsu = LiteEthTSU(sys_clk_freq)

        # 2. TX/RX Helpers.
        self.tx         = tx    = LiteEthPTPTX(tsu)
        self.rx_event   = rx_ev = LiteEthPTPRX(PTP_EVENT_PORT, sys_clk_freq=sys_clk_freq)
        self.rx_general = rx_ge = LiteEthPTPRX(PTP_GENERAL_PORT, sys_clk_freq=sys_clk_freq)

        # 3. Timestamp Latching.
        self.latcher = latcher = LiteEthPTPRxTimestamp(event_port, general_port)

        # 4. Clock Servo.
        self.servo = servo = LiteEthPTPClockServo(tsu)

        # 5. Protocol Control (FSM).
        self.control = control = LiteEthPTPControl(
            tsu, tx, rx_ev, rx_ge, servo, latcher,
            event_source            = event_port.source,
            general_source          = general_port.source,
            enable                  = self.enable,
            require_announce        = self.require_announce,
            announce_timeout_cycles = (
                None if announce_timeout is None else int(announce_timeout*sys_clk_freq)
            )
        )

        # I/O Wiring.
        # -----------
        self.comb += [
            # RX.
            event_port.source.connect(rx_ev.sink),
            general_port.source.connect(rx_ge.sink),
            rx_ev.domain.eq(self.domain),
            rx_ge.domain.eq(self.domain),

            # TX.
            tx.source.connect(event_port.sink),
            tx.domain.eq(self.domain),
            tx.clock_id.eq(self.clock_id),

            # Use the dynamically learned master IP from the control module.
            tx.ip_address.eq(control.master_ip),

            tx.src_port.eq(PTP_EVENT_PORT),
            tx.dst_port.eq(PTP_EVENT_PORT),
            # Propagate P2P mode to TX helper and select message type.
            tx.p2p_mode.eq(self.p2p_mode),
            If(self.p2p_mode,
                tx.msg_type.eq(PTP_MSG_PDELAY_REQ)
            ).Else(
                tx.msg_type.eq(PTP_MSG_DELAY_REQ)
            )
        ]

        # Top-level status outputs.
        self.comb += [
            self.locked.eq(control.locked),
            self.master_ip.eq(control.master_ip),
        ]

        self.sync += [
            If(rx_ev.present,
                self.last_rx_msg_type.eq(rx_ev.msg_type),
                self.last_rx_seq_id.eq(rx_ev.seq_id)
            ).Elif(rx_ge.present,
                self.last_rx_msg_type.eq(rx_ge.msg_type),
                self.last_rx_seq_id.eq(rx_ge.seq_id)
            ),
            If(rx_ev.invalid_header | rx_ge.invalid_header,
                self.invalid_header_count.eq(self.invalid_header_count + 1)
            ),
            If(control.peer_mismatch,
                self.wrong_peer_count.eq(self.wrong_peer_count + 1)
            ),
            If(control.requester_mismatch,
                self.wrong_requester_count.eq(self.wrong_requester_count + 1)
            ),
            If(rx_ev.timeout_error | rx_ge.timeout_error,
                self.rx_timeout_count.eq(self.rx_timeout_count + 1)
            ),
            If(control.announce_expired,
                self.announce_expiry_count.eq(self.announce_expiry_count + 1)
            )
        ]

        # CSRs.
        # -----
        self._locked                = CSRStatus(description="PTP lock status.")
        self._master_ip             = CSRStatus(32, description="Master IPv4.")
        self._last_rx_msg_type      = CSRStatus( 4, description="Last RX msg type.")
        self._last_rx_seq_id        = CSRStatus(16, description="Last RX seq id.")
        self._invalid_header_count  = CSRStatus(32, description="Invalid header count.")
        self._wrong_peer_count      = CSRStatus(32, description="Wrong peer count.")
        self._wrong_requester_count = CSRStatus(32, description="Wrong requester count.")
        self._rx_timeout_count      = CSRStatus(32, description="RX timeout count.")
        self._announce_expiry_count = CSRStatus(32, description="Announce expiry count.")
        self.comb += [
            self._locked.status.eq(self.locked),
            self._master_ip.status.eq(self.master_ip),
            self._last_rx_msg_type.status.eq(self.last_rx_msg_type),
            self._last_rx_seq_id.status.eq(self.last_rx_seq_id),
            self._invalid_header_count.status.eq(self.invalid_header_count),
            self._wrong_peer_count.status.eq(self.wrong_peer_count),
            self._wrong_requester_count.status.eq(self.wrong_requester_count),
            self._rx_timeout_count.status.eq(self.rx_timeout_count),
            self._announce_expiry_count.status.eq(self.announce_expiry_count),
        ]

        # Monitor (optional, enabled by default).
        # ---------------------------------------
        if monitor_debug is not None:
            self.add_monitor(debug=monitor_debug)

        # 6. Timeout Timer.
        self.timeout_timer = timeout_timer = WaitTimer(int(timeout*sys_clk_freq))
        self.comb += [
            timeout_timer.wait.eq(
                self.enable &
                (self.locked == 0) &
                ( ~timeout_timer.done )
            ),
            self.timeout.eq(timeout_timer.done),
        ]

    def add_monitor(self, debug=True):
        """Add snapshot-based monitoring CSRs (common + optional debug)."""
        self.monitor = LiteEthPTPMonitor(self.tsu, self.servo, self, debug=debug)


# LiteEthPTPMonitor --------------------------------------------------------------------------------

class LiteEthPTPMonitor(LiteXModule):
    """
    PTP Snapshot Monitor.

    Provides coherent CSR snapshots of PTP state for Etherbone reads.
    Common mode has essential status; debug mode adds full timestamp/servo diagnostics.

    Parameters:
    - tsu   : LiteEthTSU instance.
    - servo : LiteEthPTPClockServo instance.
    - ptp   : LiteEthPTP instance.
    - debug : Enable extended debug CSRs.
    """
    def __init__(self, tsu, servo, ptp, debug=True):
        addend_frac_bits = len(tsu.addend_frac)

        # Serve-time Latches.
        last_t1               = Signal(80)
        last_t2               = Signal(80)
        last_t3               = Signal(80)
        last_t4               = Signal(80)
        last_phase            = Signal((64, True))
        last_delay            = Signal((64, True))
        last_sample_valid     = Signal()
        serve_count           = Signal(32)
        step_count            = Signal(32)
        reject_count          = Signal(32)
        serve_tsu_seconds     = Signal(48)
        serve_tsu_nanoseconds = Signal(32)
        serve_outlier         = Signal()
        serve_sec_adjust      = Signal()
        serve_coarse          = Signal()
        serve_valid           = Signal()
        serve_offset          = Signal((64, True))
        serve_addend_next     = Signal(len(servo._addend_next))
        serve_freq_step       = Signal((32, True))

        self.sync += [
            If(servo.serve_done,
                serve_count.eq(serve_count + 1),
                serve_tsu_seconds.eq(tsu.seconds),
                serve_tsu_nanoseconds.eq(tsu.nanoseconds),
                serve_outlier.eq(servo._exchange_outlier),
                serve_sec_adjust.eq(servo._sec_adjust_needed),
                serve_coarse.eq(servo._coarse_step_needed),
                serve_valid.eq(servo.sample_valid),
                serve_offset.eq(tsu.offset),
                serve_addend_next.eq(servo._addend_next),
                serve_freq_step.eq(servo._freq_step[:32]),
                If(servo.sample_valid,
                    last_t1.eq(servo.t1),
                    last_t2.eq(servo.t2),
                    last_t3.eq(servo.t3),
                    last_t4.eq(servo.t4),
                    last_phase.eq(servo.phase_error),
                    last_delay.eq(servo.mean_path_delay),
                    last_sample_valid.eq(servo.sample_valid),
                ),
                If(~servo.sample_valid,
                    reject_count.eq(reject_count + 1)
                )
            ),
            If(tsu.step,
                step_count.eq(step_count + 1)
            ),
        ]

        # Snapshot trigger.
        # -----------------
        self._snapshot = CSRStorage(description="Write to latch a coherent PTP monitor snapshot.")

        # Common CSRs (always present).
        # -----------------------------
        self._locked                = CSRStatus( 1, description="Locked flag.")
        self._master_ip             = CSRStatus(32, description="Master IPv4.")
        self._tsu_seconds           = CSRStatus(48, description="TSU seconds.")
        self._tsu_nanoseconds       = CSRStatus(32, description="TSU nanoseconds.")
        self._addend                = CSRStatus(32, description="TSU addend integer.")
        self._addend_frac           = CSRStatus(addend_frac_bits, description="TSU addend frac.")
        self._phase                 = CSRStatus(64, description="Phase error.")
        self._delay                 = CSRStatus(64, description="Path delay.")
        self._serve_count           = CSRStatus(32, description="Servo serve count.")
        self._step_count            = CSRStatus(32, description="Coarse-step count.")
        self._reject_count          = CSRStatus(32, description="Outlier reject count.")
        self._last_sample_valid     = CSRStatus( 1, description="Last exchange validity.")
        self._serve_tsu_seconds     = CSRStatus(48, description="Serve TSU seconds.")
        self._serve_tsu_nanoseconds = CSRStatus(32, description="Serve TSU nanoseconds.")
        self._serve_flags           = CSRStatus( 8, description="Serve flags: V/O/S/C.")

        # Snapshot Registers (common).
        # ----------------------------
        m = Record([
            ("locked",                1),
            ("master_ip",             32),
            ("tsu_seconds",           48),
            ("tsu_nanoseconds",       32),
            ("addend",                32),
            ("addend_frac",           addend_frac_bits),
            ("phase",                 64),
            ("delay",                 64),
            ("serve_count",           32),
            ("step_count",            32),
            ("reject_count",          32),
            ("last_sample_valid",      1),
            ("serve_tsu_seconds",     48),
            ("serve_tsu_nanoseconds", 32),
            ("serve_flags",            8),
        ])

        self.sync += If(self._snapshot.re,
            m.locked.eq(ptp.locked),
            m.master_ip.eq(ptp.master_ip),
            m.tsu_seconds.eq(tsu.seconds),
            m.tsu_nanoseconds.eq(tsu.nanoseconds),
            m.addend.eq(tsu.addend),
            m.addend_frac.eq(tsu.addend_frac),
            m.phase.eq(last_phase),
            m.delay.eq(last_delay),
            m.serve_count.eq(serve_count),
            m.step_count.eq(step_count),
            m.reject_count.eq(reject_count),
            m.last_sample_valid.eq(last_sample_valid),
            m.serve_tsu_seconds.eq(serve_tsu_seconds),
            m.serve_tsu_nanoseconds.eq(serve_tsu_nanoseconds),
            m.serve_flags.eq(Cat(
                serve_valid, serve_outlier, serve_sec_adjust, serve_coarse
            )),
        )

        self.comb += [
            self._locked.status.eq(m.locked),
            self._master_ip.status.eq(m.master_ip),
            self._tsu_seconds.status.eq(m.tsu_seconds),
            self._tsu_nanoseconds.status.eq(m.tsu_nanoseconds),
            self._addend.status.eq(m.addend),
            self._addend_frac.status.eq(m.addend_frac),
            self._phase.status.eq(m.phase),
            self._delay.status.eq(m.delay),
            self._serve_count.status.eq(m.serve_count),
            self._step_count.status.eq(m.step_count),
            self._reject_count.status.eq(m.reject_count),
            self._last_sample_valid.status.eq(m.last_sample_valid),
            self._serve_tsu_seconds.status.eq(m.serve_tsu_seconds),
            self._serve_tsu_nanoseconds.status.eq(m.serve_tsu_nanoseconds),
            self._serve_flags.status.eq(m.serve_flags),
        ]

        # Debug CSRs (only when debug=True).
        # ---------------------------------
        if debug:
            self._shadow_addend     = CSRStatus(32, description="Shadow addend.")
            self._shadow_frac       = CSRStatus(addend_frac_bits, description="Shadow frac.")
            self._offset            = CSRStatus(64, description="Offset.")
            self._t1                = CSRStatus(80, description="Last t1.")
            self._t2                = CSRStatus(80, description="Last t2.")
            self._t3                = CSRStatus(80, description="Last t3.")
            self._t4                = CSRStatus(80, description="Last t4.")
            self._live_sample_valid = CSRStatus( 1, description="Live validity.")
            self._live_t1           = CSRStatus(80, description="Live t1.")
            self._live_t2           = CSRStatus(80, description="Live t2.")
            self._live_t3           = CSRStatus(80, description="Live t3.")
            self._live_t4           = CSRStatus(80, description="Live t4.")
            self._live_dt21         = CSRStatus(64, description="Live dt21.")
            self._live_dt43         = CSRStatus(64, description="Live dt43.")
            self._live_phase        = CSRStatus(64, description="Live phase.")
            self._live_delay        = CSRStatus(64, description="Live delay.")
            self._serve_offset      = CSRStatus(64, description="Serve offset.")
            self._serve_addend_next = CSRStatus(
                len(servo._addend_next), description="Serve addend_next.")
            self._serve_freq_step   = CSRStatus(32, description="Serve freq_step.")

            # Debug Snapshot Registers.
            # -------------------------
            d = Record([
                ("shadow_addend",     32),
                ("shadow_frac",       addend_frac_bits),
                ("offset",            64),
                ("t1",                80),
                ("t2",                80),
                ("t3",                80),
                ("t4",                80),
                ("live_valid",         1),
                ("live_t1",           80),
                ("live_t2",           80),
                ("live_t3",           80),
                ("live_t4",           80),
                ("live_dt21",         64),
                ("live_dt43",         64),
                ("live_phase",        64),
                ("live_delay",        64),
                ("serve_offset",      64),
                ("serve_addend_next", len(servo._addend_next)),
                ("serve_freq_step",   32),
            ])

            self.sync += If(self._snapshot.re,
                d.shadow_addend.eq(
                    servo._shadow_addend[addend_frac_bits:addend_frac_bits + len(tsu.addend)]
                ),
                d.shadow_frac.eq(servo._shadow_addend[:addend_frac_bits]),
                d.offset.eq(tsu.offset[0:64]),
                d.t1.eq(last_t1),
                d.t2.eq(last_t2),
                d.t3.eq(last_t3),
                d.t4.eq(last_t4),
                d.live_valid.eq(servo.sample_valid),
                d.live_t1.eq(servo.t1),
                d.live_t2.eq(servo.t2),
                d.live_t3.eq(servo.t3),
                d.live_t4.eq(servo.t4),
                d.live_dt21.eq(servo.dt21),
                d.live_dt43.eq(servo.dt43),
                d.live_phase.eq(servo.phase_error),
                d.live_delay.eq(servo.mean_path_delay),
                d.serve_offset.eq(serve_offset),
                d.serve_addend_next.eq(serve_addend_next),
                d.serve_freq_step.eq(serve_freq_step),
            )

            self.comb += [
                self._shadow_addend.status.eq(d.shadow_addend),
                self._shadow_frac.status.eq(d.shadow_frac),
                self._offset.status.eq(d.offset),
                self._t1.status.eq(d.t1),
                self._t2.status.eq(d.t2),
                self._t3.status.eq(d.t3),
                self._t4.status.eq(d.t4),
                self._live_sample_valid.status.eq(d.live_valid),
                self._live_t1.status.eq(d.live_t1),
                self._live_t2.status.eq(d.live_t2),
                self._live_t3.status.eq(d.live_t3),
                self._live_t4.status.eq(d.live_t4),
                self._live_dt21.status.eq(d.live_dt21),
                self._live_dt43.status.eq(d.live_dt43),
                self._live_phase.status.eq(d.live_phase),
                self._live_delay.status.eq(d.live_delay),
                self._serve_offset.status.eq(d.serve_offset),
                self._serve_addend_next.status.eq(d.serve_addend_next),
                self._serve_freq_step.status.eq(d.serve_freq_step),
            ]

