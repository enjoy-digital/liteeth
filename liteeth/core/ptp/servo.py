#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


import math

from migen import *

from litex.gen import LiteXModule


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
            clk_freq   = (1 << (32 + frac_bits)) / max(1, addend)
            lsb_drift  = clk_freq * 1e9 / (1 << (32 + frac_bits))
            return max(1, math.ceil(math.log2(max(1, lsb_drift))) + 1)

        addend_frac_bits = len(tsu.addend_frac)
        nominal_addend_full = (
            (tsu.addend.reset.value << addend_frac_bits) |
            tsu.addend_frac.reset.value
        )
        one_billion      = int(1_000_000_000)

        # Phase servo gain (kp=1: full correction each exchange).
        kp = 1

        # Frequency integrator parameters.
        freq_shift       = compute_freq_shift(nominal_addend_full, addend_frac_bits)
        freq_deadband    = 256               # ns: ignore phase errors below this.
        freq_max_step    = 1 << max(0, addend_frac_bits - 3)  # Max step per serve.
        phase_clamp      = one_billion - 1   # Max phase correction (ns).

        # Addend clamping (nominal ± 1 integer unit).
        min_addend_full = max(1, nominal_addend_full - (1 << addend_frac_bits))
        max_addend_full = min(
            (1 << (32 + addend_frac_bits)) - 1,
            nominal_addend_full + (1 << addend_frac_bits)
        )

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
        shadow_addend       = Signal(full_addend_bits, reset=nominal_addend_full)
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
                addend_next.eq(min_addend_full)
            ).Elif(freq_sum > max_addend_full,
                addend_next.eq(max_addend_full)
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
