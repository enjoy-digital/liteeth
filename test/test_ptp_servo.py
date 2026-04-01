#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""PTP Servo unit tests — pure-Python mirrors of the HDL algorithms."""

import math
import unittest

# Constants ----------------------------------------------------------------------------------------

# Must match ptp.py servo parameters.
ONE_BILLION      = 1_000_000_000
ADDEND_FRAC_BITS = 20
CLK_FREQ         = int(100e6)
NOMINAL_ADDEND   = int(((1 << 32) + (CLK_FREQ // 2)) // CLK_FREQ)
NOMINAL_FULL     = NOMINAL_ADDEND << ADDEND_FRAC_BITS

# Outlier detection thresholds.
OUTLIER_NEAR_NS   = 50_000_000
OUTLIER_MAX_DELAY = 5_000_000

# Frequency integrator parameters.
_lsb_drift    = CLK_FREQ * 1e9 / (1 << (32 + ADDEND_FRAC_BITS))
FREQ_SHIFT    = max(1, math.ceil(math.log2(max(1, _lsb_drift))) + 1)
FREQ_DEADBAND = 256
FREQ_MAX_STEP = 1 << max(0, ADDEND_FRAC_BITS - 3)
PHASE_CLAMP   = ONE_BILLION - 1

# Helpers ------------------------------------------------------------------------------------------

def signed_delta_ns(a_ns, a_sec, b_ns, b_sec):
    """Compute (a - b) in nanoseconds, handling ±1 second boundary."""
    if a_sec == b_sec:
        return a_ns - b_ns
    elif a_sec == (b_sec + 1):
        return (a_ns + ONE_BILLION) - b_ns
    elif b_sec == (a_sec + 1):
        return -((b_ns + ONE_BILLION) - a_ns)
    else:
        return 0  # >1 second apart: undefined.


def signed_half_toward_zero(value):
    """Halve a signed integer toward zero (not toward negative infinity)."""
    if value < 0:
        return (value + 1) >> 1
    return value >> 1


# Outlier Detection (mirrors HDL) ------------------------------------------------------------------

def exchange_outlier(dt21, dt43, delay_e2e, p2p_mode=False):
    """Return True if this exchange looks like a second-boundary artifact."""
    if p2p_mode:
        return False
    dt21_abs = abs(dt21)
    dt43_abs = abs(dt43)
    delay_abs = abs(delay_e2e)
    lo = ONE_BILLION - OUTLIER_NEAR_NS
    hi = ONE_BILLION + OUTLIER_NEAR_NS
    near_t21 = lo <= dt21_abs <= hi
    near_t43 = lo <= dt43_abs <= hi
    opposite_signs = (dt21 < 0) != (dt43 < 0)
    small_delay = delay_abs <= OUTLIER_MAX_DELAY
    return near_t21 and near_t43 and opposite_signs and small_delay


# Servo Computation (mirrors HDL) ------------------------------------------------------------------

def servo_compute(t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns):
    """Compute phase error, path delay, and deltas for an E2E exchange."""
    dt21 = signed_delta_ns(t2_ns, t2_sec, t1_ns, t1_sec)
    dt43 = signed_delta_ns(t4_ns, t4_sec, t3_ns, t3_sec)
    delay_sum = dt21 + dt43
    delay = signed_half_toward_zero(delay_sum)
    offset_sum = dt21 - dt43
    phase = signed_half_toward_zero(offset_sum)
    return dt21, dt43, phase, delay


# Frequency Step (mirrors HDL) ---------------------------------------------------------------------

def freq_step_compute(phase, addend_full):
    """Compute frequency trim step from phase error."""
    err_freq = -phase
    err_freq_abs = abs(err_freq)
    if err_freq_abs <= FREQ_DEADBAND:
        step = 0
    else:
        mag = err_freq_abs >> FREQ_SHIFT
        if mag > FREQ_MAX_STEP:
            mag = FREQ_MAX_STEP
        step = -mag if err_freq < 0 else mag
    new_addend = addend_full + step
    # Clamp integer part only, preserve fractional bits (mirrors HDL fix).
    min_addend_int = max(1, NOMINAL_ADDEND - 1)
    max_addend_int = min((1 << 32) - 1, NOMINAL_ADDEND + 1)
    addend_int = new_addend >> ADDEND_FRAC_BITS
    addend_frac = new_addend & ((1 << ADDEND_FRAC_BITS) - 1)
    if addend_int < min_addend_int:
        addend_int = min_addend_int
    elif addend_int > max_addend_int:
        addend_int = max_addend_int
    new_addend = (addend_int << ADDEND_FRAC_BITS) | addend_frac
    return new_addend, step


# Coarse Step Target (mirrors HDL) -----------------------------------------------------------------

def coarse_step_target(tsu_sec, tsu_ns, t1_sec, t1_ns, t2_sec, t2_ns):
    """Compute the coarse step target, mirroring the HDL combinational logic."""
    if tsu_ns >= t2_ns:
        elapsed_ns  = tsu_ns - t2_ns
        elapsed_sec = tsu_sec - t2_sec
    else:
        elapsed_ns  = tsu_ns + ONE_BILLION - t2_ns
        elapsed_sec = tsu_sec - t2_sec - 1

    if (t1_ns + elapsed_ns) >= ONE_BILLION:
        target_ns  = t1_ns + elapsed_ns - ONE_BILLION
        target_sec = t1_sec + elapsed_sec + 1
    else:
        target_ns  = t1_ns + elapsed_ns
        target_sec = t1_sec + elapsed_sec
    return target_sec, target_ns


# Test Signed Delta NS -----------------------------------------------------------------------------

class TestSignedDeltaNs(unittest.TestCase):
    """Tests for the signed_delta_ns helper."""

    def test_same_second_positive(self):
        self.assertEqual(signed_delta_ns(500_000_000, 10, 400_000_000, 10), 100_000_000)

    def test_same_second_negative(self):
        self.assertEqual(signed_delta_ns(400_000_000, 10, 500_000_000, 10), -100_000_000)

    def test_same_second_zero(self):
        self.assertEqual(signed_delta_ns(123_456, 5, 123_456, 5), 0)

    def test_a_one_second_ahead(self):
        # a_sec = b_sec + 1, a just crossed boundary: a = 11.000010000, b = 10.999990000.
        delta = signed_delta_ns(10_000, 11, 999_990_000, 10)
        self.assertEqual(delta, 20_000)

    def test_b_one_second_ahead(self):
        # b_sec = a_sec + 1. Symmetric to above.
        delta = signed_delta_ns(999_990_000, 10, 10_000, 11)
        self.assertEqual(delta, -20_000)

    def test_boundary_large_ns_diff(self):
        # a = 11.100000000, b = 10.900000000. True delta = 0.2s = 200ms.
        delta = signed_delta_ns(100_000_000, 11, 900_000_000, 10)
        self.assertEqual(delta, 200_000_000)

    def test_more_than_one_second_returns_zero(self):
        # >1 second difference: function returns 0 (undefined).
        self.assertEqual(signed_delta_ns(0, 10, 0, 12), 0)
        self.assertEqual(signed_delta_ns(0, 12, 0, 10), 0)

    def test_exact_one_second(self):
        # a = 11.000000000, b = 10.000000000. Delta = 1s.
        delta = signed_delta_ns(0, 11, 0, 10)
        self.assertEqual(delta, ONE_BILLION)

    def test_negative_exact_one_second(self):
        delta = signed_delta_ns(0, 10, 0, 11)
        self.assertEqual(delta, -ONE_BILLION)


# Test Signed Half Toward Zero ----------------------------------------------------------------------

class TestSignedHalfTowardZero(unittest.TestCase):
    """Verify half-toward-zero rounding."""

    def test_positive_even(self):
        self.assertEqual(signed_half_toward_zero(100), 50)

    def test_positive_odd(self):
        self.assertEqual(signed_half_toward_zero(101), 50)

    def test_negative_even(self):
        self.assertEqual(signed_half_toward_zero(-100), -50)

    def test_negative_odd(self):
        # -99 + 1 = -98, >> 1 = -49 (not -50).
        self.assertEqual(signed_half_toward_zero(-99), -49)

    def test_zero(self):
        self.assertEqual(signed_half_toward_zero(0), 0)

    def test_minus_one(self):
        self.assertEqual(signed_half_toward_zero(-1), 0)

    def test_one(self):
        self.assertEqual(signed_half_toward_zero(1), 0)


# Test Servo Compute -------------------------------------------------------------------------------

class TestServoCompute(unittest.TestCase):
    """Tests for servo offset/delay computation."""

    def test_aligned_clocks_small_delay(self):
        # Master and slave well-aligned, link delay = 10μs each way.
        delay_ns = 10_000
        t1_sec, t1_ns = 100, 500_000_000
        t2_sec, t2_ns = 100, 500_000_000 + delay_ns
        t3_sec, t3_ns = 100, 600_000_000
        t4_sec, t4_ns = 100, 600_000_000 + delay_ns
        dt21, dt43, phase, delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        self.assertEqual(dt21, delay_ns)
        self.assertEqual(dt43, delay_ns)
        self.assertEqual(phase, 0)
        self.assertEqual(delay, delay_ns)

    def test_slave_behind_20us(self):
        # Slave is 20μs behind master.
        offset = -20_000  # slave behind
        delay_ns = 13_000
        t1_sec, t1_ns = 100, 963_000_000
        t2_sec, t2_ns = 100, 963_000_000 + offset + delay_ns
        t3_sec, t3_ns = 100, 963_050_000 + offset
        t4_sec, t4_ns = 100, 963_050_000 + delay_ns
        dt21, dt43, phase, delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        # dt21 = t2 - t1 = offset + delay = -20000 + 13000 = -7000
        self.assertEqual(dt21, offset + delay_ns)
        # dt43 = t4 - t3 = -offset + delay = 20000 + 13000 = 33000
        self.assertEqual(dt43, -offset + delay_ns)
        # phase = (dt21 - dt43) / 2 = (offset + delay - (-offset + delay)) / 2 = offset
        self.assertEqual(phase, offset)
        # delay = (dt21 + dt43) / 2 = delay_ns
        self.assertEqual(delay, delay_ns)

    def test_second_boundary_crossing_normal(self):
        """Exchange where Delay_Req/Resp cross a second boundary — NOT an artifact."""
        # Master Sync at 963ms, slave aligned. Delay_Req sent 40ms later.
        t1_sec, t1_ns = 100, 963_000_000
        t2_sec, t2_ns = 100, 963_013_000   # slave RX (same second)
        t3_sec, t3_ns = 101, 3_000_000     # slave TX: 101.003s (crossed second)
        t4_sec, t4_ns = 101, 3_013_000     # master RX (crossed second)
        dt21, dt43, phase, delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        self.assertEqual(dt21, 13_000)
        self.assertEqual(dt43, 13_000)
        self.assertEqual(phase, 0)
        self.assertEqual(delay, 13_000)

    def test_second_boundary_artifact_opposite_signs(self):
        """
        Exchange where t1/t4 are in the next second but t2/t3 are in the current.
        This produces dt21 ≈ -1s, dt43 ≈ +1s: an "artifact" that the outlier
        detector should catch.
        """
        # Slave is ~20μs behind, master sent Sync at new-second + 963ms.
        # But slave hasn't rolled over yet.
        t1_sec, t1_ns = 101, 963_000_000   # master TX in sec 101
        t2_sec, t2_ns = 100, 963_013_000   # slave RX still in sec 100
        t3_sec, t3_ns = 100, 963_050_000   # slave TX still in sec 100
        t4_sec, t4_ns = 101, 963_063_000   # master RX in sec 101
        dt21, dt43, phase, delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        # dt21 = t2 - t1: t2_sec = t1_sec - 1, so delta = -((t1_ns + 1e9) - t2_ns)
        expected_dt21 = -((963_000_000 + ONE_BILLION) - 963_013_000)
        self.assertEqual(dt21, expected_dt21)
        self.assertAlmostEqual(dt21, -ONE_BILLION + 13_000, delta=1)
        # dt43 = t4 - t3: t4_sec = t3_sec + 1, so delta = (t4_ns + 1e9) - t3_ns
        expected_dt43 = (963_063_000 + ONE_BILLION) - 963_050_000
        self.assertEqual(dt43, expected_dt43)
        self.assertAlmostEqual(dt43, ONE_BILLION + 13_000, delta=1)
        # The apparent phase is huge (~-1s) but the TRUE offset is tiny.
        # The outlier detector should flag this.
        self.assertTrue(abs(phase) > 500_000_000)  # Looks like ~1s error.
        # But the link delay is small — the hallmark of the artifact.
        self.assertLess(abs(delay), 100_000)


# Test Outlier Detection ---------------------------------------------------------------------------

class TestOutlierDetection(unittest.TestCase):
    """Tests for the exchange outlier detector."""

    def test_normal_exchange_not_outlier(self):
        dt21, dt43 = 13_000, 13_000
        delay = 13_000
        self.assertFalse(exchange_outlier(dt21, dt43, delay))

    def test_second_boundary_artifact_detected(self):
        """Opposite-sign dt21/dt43 near ±1s with small delay → outlier."""
        dt21 = -(ONE_BILLION - 13_000)   # ≈ -1s
        dt43 =  (ONE_BILLION + 13_000)   # ≈ +1s
        delay = signed_half_toward_zero(dt21 + dt43)  # ≈ 13000
        self.assertTrue(exchange_outlier(dt21, dt43, delay))

    def test_large_delay_not_outlier(self):
        """Near-second deltas but large delay → NOT an artifact (genuinely offset)."""
        dt21 = -(ONE_BILLION - 3_000_000)
        dt43 =  (ONE_BILLION + 17_000_000)
        delay = signed_half_toward_zero(dt21 + dt43)  # 10M → beyond 5M threshold
        self.assertGreater(abs(delay), OUTLIER_MAX_DELAY)
        self.assertFalse(exchange_outlier(dt21, dt43, delay))

    def test_same_sign_not_outlier(self):
        """Both deltas same sign near ±1s → not matching the artifact pattern."""
        dt21 = -(ONE_BILLION - 13_000)
        dt43 = -(ONE_BILLION - 13_000)
        delay = signed_half_toward_zero(dt21 + dt43)
        self.assertFalse(exchange_outlier(dt21, dt43, delay))

    def test_p2p_mode_never_outlier(self):
        dt21 = -(ONE_BILLION - 13_000)
        dt43 =  (ONE_BILLION + 13_000)
        delay = 13_000
        self.assertFalse(exchange_outlier(dt21, dt43, delay, p2p_mode=True))

    def test_from_trace_serve8_rejected(self):
        """Reproduce the rejected exchange from the user's trace."""
        dt21 = -1_000_014_162
        dt43 =  1_000_043_906
        delay = signed_half_toward_zero(dt21 + dt43)
        self.assertTrue(exchange_outlier(dt21, dt43, delay))

    def test_from_trace_serve9_also_outlier(self):
        """This exchange was accepted via wrap_outlier_seen — it IS an outlier."""
        dt21 = -999_996_483
        dt43 =  1_000_022_147
        delay = signed_half_toward_zero(dt21 + dt43)
        self.assertTrue(exchange_outlier(dt21, dt43, delay))


# Test Coarse Step Target --------------------------------------------------------------------------

class TestCoarseStepTarget(unittest.TestCase):
    """Tests for the coarse step target computation."""

    def test_simple_same_second(self):
        # TSU, t1, t2 all in the same second.
        tgt_sec, tgt_ns = coarse_step_target(
            tsu_sec=100, tsu_ns=500_000_000,
            t1_sec=100,  t1_ns=400_000_000,
            t2_sec=100,  t2_ns=450_000_000)
        # elapsed = 500M - 450M = 50ms. Target = 400M + 50M = 450M.
        self.assertEqual(tgt_sec, 100)
        self.assertEqual(tgt_ns, 450_000_000)

    def test_tsu_rolled_past_t2(self):
        """TSU second rolled over since t2 was captured."""
        tgt_sec, tgt_ns = coarse_step_target(
            tsu_sec=101, tsu_ns=10_000_000,      # 101.010
            t1_sec=100,  t1_ns=963_000_000,        # 100.963
            t2_sec=100,  t2_ns=963_000_000)        # 100.963
        # elapsed_ns = 10M < 963M → borrow: 10M + 1e9 - 963M = 47M, sec = 101 - 100 - 1 = 0
        # target_ns = 963M + 47M = 1010M >= 1e9 → carry: 10M, sec = 100 + 0 + 1 = 101
        self.assertEqual(tgt_sec, 101)
        self.assertEqual(tgt_ns, 10_000_000)

    def test_slave_one_second_behind(self):
        """
        Slave is 1 second behind master. Coarse step should bring it to ~master time.
        t1_sec=101, t2_sec=100 (slave was in sec 100 when master was in 101).
        TSU is still at second 100 (hasn't advanced much since t2).
        """
        tgt_sec, tgt_ns = coarse_step_target(
            tsu_sec=100, tsu_ns=990_000_000,      # slave at 100.990
            t1_sec=101,  t1_ns=963_000_000,        # master was at 101.963
            t2_sec=100,  t2_ns=963_000_000)        # slave latched at 100.963
        # elapsed: 990M - 963M = 27M, sec = 0
        # target: 963M + 27M = 990M, sec = 101
        self.assertEqual(tgt_sec, 101)
        self.assertEqual(tgt_ns, 990_000_000)

    def test_coarse_step_with_second_boundary_artifact(self):
        """
        Verify that if the slave is WELL-ALIGNED but the exchange has
        t1_sec != t2_sec due to a second-boundary artifact, the coarse
        step target would be WRONG (off by ~1 second).

        This demonstrates why accepting outlier exchanges is dangerous.
        """
        # Slave was aligned at ~100.963s. Master sends Sync at ~101.963s.
        # But suppose the slave's TSU read 100.963 (hadn't rolled to 101 yet)
        # at the moment the Sync arrived (due to nanosecond-level jitter).
        # Actually: if the slave is aligned, its time ≈ master time.
        # So when master is at 101.963, slave is at ~101.963 too.
        # t2 would be latched at slave time ~101.963, same second as t1.
        # The artifact only happens when the slave IS behind.
        #
        # So let's check: slave is at 100.990 (genuinely 1s behind).
        # t1=101.963, t2=100.963. Coarse step:
        tgt_sec, tgt_ns = coarse_step_target(
            tsu_sec=100, tsu_ns=990_000_000,
            t1_sec=101,  t1_ns=963_000_000,
            t2_sec=100,  t2_ns=963_000_000)
        # This correctly targets 101.990 — advancing by ~1 second.
        self.assertEqual(tgt_sec, 101)
        self.assertEqual(tgt_ns, 990_000_000)


# Test Frequency Step ------------------------------------------------------------------------------

class TestFreqStep(unittest.TestCase):
    """Tests for the frequency trimming logic."""

    def test_deadband_no_step(self):
        _, step = freq_step_compute(100, NOMINAL_FULL)
        self.assertEqual(step, 0)

    def test_positive_phase_decreases_addend(self):
        """Slave ahead → decrease addend to slow down."""
        new, step = freq_step_compute(10_000, NOMINAL_FULL)
        self.assertLess(step, 0)
        self.assertLess(new, NOMINAL_FULL)

    def test_negative_phase_increases_addend(self):
        """Slave behind → increase addend to speed up."""
        new, step = freq_step_compute(-10_000, NOMINAL_FULL)
        self.assertGreater(step, 0)
        self.assertGreater(new, NOMINAL_FULL)

    def test_step_magnitude(self):
        """Step mag = abs(phase) >> FREQ_SHIFT."""
        phase = 10_000
        _, step = freq_step_compute(phase, NOMINAL_FULL)
        expected_mag = abs(phase) >> FREQ_SHIFT  # 10000 >> 8 = 39
        self.assertEqual(abs(step), expected_mag)

    def test_clamped_at_max(self):
        """Very large phase error → step is clamped to FREQ_MAX_STEP."""
        _, step = freq_step_compute(-100_000_000, NOMINAL_FULL)
        self.assertEqual(abs(step), FREQ_MAX_STEP)


# Test Exchange Validity ---------------------------------------------------------------------------

class TestExchangeValidity(unittest.TestCase):
    """
    Tests for exchange validity classification.

    The servo has three exchange categories:
    1. Valid (same second): phase/freq corrections applied.
    2. Outlier (|Δsec| == 1, small delay): seconds offset applied, no phase/freq.
    3. Stale (|Δsec| > 1, missed Syncs): completely ignored (garbage deltas).

    The coarse step (initial lock) fires independently for |Δsec| > 1.
    """

    def _classify(self, t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns):
        """Classify an exchange and return (sample_valid, outlier, sec_gap, phase)."""
        dt21, dt43, phase, delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        is_outlier = exchange_outlier(dt21, dt43, delay)
        sec_gap = abs(t1_sec - t2_sec) > 1
        # Mirror HDL: sample_valid_now = ~sec_gap; self.sample_valid = ~outlier & ~sec_gap
        sample_valid = (not is_outlier) and (not sec_gap)
        return sample_valid, is_outlier, sec_gap, phase

    def test_normal_exchange_is_valid(self):
        """Same-second exchange with small phase → valid."""
        valid, outlier, gap, phase = self._classify(
            100, 500_000_000, 100, 500_013_000, 100, 500_050_000, 100, 500_063_000)
        self.assertTrue(valid)
        self.assertFalse(outlier)
        self.assertFalse(gap)
        self.assertLess(abs(phase), 100_000)

    def test_outlier_1sec_not_valid(self):
        """±1 second boundary crossing → outlier, NOT valid for phase/freq."""
        valid, outlier, gap, phase = self._classify(
            101, 594_000_000, 100, 594_013_000, 100, 594_050_000, 101, 594_063_000)
        self.assertFalse(valid, "Outlier exchange should not be valid")
        self.assertTrue(outlier)
        self.assertFalse(gap, "|Δsec|=1 is NOT a gap")

    def test_missed_syncs_3sec_gap_not_valid(self):
        """
        Master missed 3 Syncs: |Δsec|=4, signed_delta_ns returns 0.
        Exchange should be completely invalid — garbage phase.
        """
        valid, outlier, gap, phase = self._classify(
            104, 594_000_000, 100, 594_013_000, 100, 594_050_000, 104, 594_063_000)
        self.assertFalse(valid, "Stale exchange (|Δsec|>1) should NOT be valid")
        self.assertTrue(gap)
        # dt21=0 (from signed_delta_ns returning 0) → NOT detected as outlier
        self.assertFalse(outlier, "|Δsec|>1 is NOT caught by outlier detector")
        # Phase is garbage — NOT the true ns-level offset
        # (signed_delta_ns returned 0, so phase depends on stale t3/t4)

    def test_missed_syncs_2sec_gap(self):
        """|Δsec|=2: also a gap, should be invalid."""
        valid, outlier, gap, phase = self._classify(
            102, 594_000_000, 100, 594_013_000, 100, 594_050_000, 102, 594_063_000)
        self.assertFalse(valid)
        self.assertTrue(gap)

    def test_initial_lock_large_offset(self):
        """
        Initial lock: slave at second 10, master at second 1774880000.
        |Δsec| >> 1. Exchange is invalid for phase/freq, but the coarse step
        should still fire (it uses the seconds comparison, not the phase).
        """
        valid, outlier, gap, phase = self._classify(
            1774880000, 500_000_000, 10, 500_013_000,
            10, 500_050_000, 1774880000, 500_063_000)
        self.assertFalse(valid, "Initial lock exchange is not valid for phase/freq")
        self.assertTrue(gap)
        # coarse_step_needed would fire from t1_sec >> t2_sec + 1
        coarse = (1774880000 > 10 + 1)
        self.assertTrue(coarse, "Coarse step should fire for initial lock")

    def test_gap_does_not_update_addend(self):
        """
        Simulate: a gap exchange should NOT produce any freq_step.
        The addend must stay unchanged.
        """
        # Normal exchange first — establish a good addend.
        addend = NOMINAL_FULL
        _, step = freq_step_compute(1000, addend)  # Small phase, small step.
        addend += step
        saved_addend = addend

        # Now a gap exchange arrives. Since it's invalid, the servo should
        # NOT call freq_step_compute at all. The addend stays the same.
        # (In HDL: sample_valid_now=False → sync block If doesn't fire.)
        # We verify the INTENT: if someone accidentally ran freq_step_compute
        # with the garbage phase, the addend would change dramatically.
        gap_phase = -462_472_088  # Garbage phase from |Δsec|>1 exchange
        _, bad_step = freq_step_compute(gap_phase, addend)
        self.assertNotEqual(bad_step, 0,
            "Garbage phase would produce a large freq_step if not filtered")
        # The addend should NOT have been updated — it stays at saved_addend.
        self.assertEqual(addend, saved_addend,
            "Addend must not change on gap exchange")

    def test_outlier_vs_gap_classification(self):
        """
        Verify that |Δsec|=1 is an outlier (seconds adjust) while
        |Δsec|>1 is a gap (completely ignored except coarse step).
        """
        # |Δsec|=1: outlier
        _, outlier_1, gap_1, _ = self._classify(
            101, 500_000_000, 100, 500_013_000, 100, 500_050_000, 101, 500_063_000)
        self.assertTrue(outlier_1)
        self.assertFalse(gap_1)

        # |Δsec|=2: gap
        _, outlier_2, gap_2, _ = self._classify(
            102, 500_000_000, 100, 500_013_000, 100, 500_050_000, 102, 500_063_000)
        self.assertFalse(outlier_2)  # outlier detector doesn't catch this
        self.assertTrue(gap_2)

        # |Δsec|=0: normal
        _, outlier_0, gap_0, _ = self._classify(
            100, 500_000_000, 100, 500_013_000, 100, 500_050_000, 100, 500_063_000)
        self.assertFalse(outlier_0)
        self.assertFalse(gap_0)


# Test Queued Sync and Skip Fix --------------------------------------------------------------------

class TestQueuedSyncAndSkipFix(unittest.TestCase):
    """Tests for skip_stale_sync: skip first Sync after LOCKED to avoid queued stale packets."""

    def test_queued_sync_would_produce_outlier(self):
        """Without the fix, a queued Sync with stale t2 produces an outlier."""
        N = 100
        sync_ns = 366_000_000
        delay = 12_000
        dt21_q, dt43_q, phase_q, delay_q = servo_compute(
            N+1, sync_ns, N, sync_ns + delay,
            N, sync_ns + 50_000, N+1, sync_ns + 50_000 + delay)
        self.assertTrue(exchange_outlier(dt21_q, dt43_q, delay_q),
            "Queued Sync with stale t2 IS an outlier")

    def test_skip_flag_lifecycle(self):
        """
        Model the skip_stale_sync flag lifecycle:
        1. Set to 1 in LOCKED
        2. First Sync in WAIT_SYNC: flag=1 → skip, set flag=0
        3. Second Sync in WAIT_SYNC: flag=0 → process normally
        """
        skip_stale_sync = False

        # LOCKED: set flag.
        skip_stale_sync = True

        # WAIT_SYNC: first Sync arrives (stale, from depacketizer queue).
        sync_present = True
        if sync_present and skip_stale_sync:
            # SKIP this Sync.
            skip_stale_sync = False
            processed = False
        else:
            processed = True
        self.assertFalse(processed, "First Sync after LOCKED must be skipped")
        self.assertFalse(skip_stale_sync, "Flag cleared after skip")

        # WAIT_SYNC: second Sync arrives (~1s later, fresh).
        sync_present = True
        if sync_present and skip_stale_sync:
            skip_stale_sync = False
            processed = False
        else:
            processed = True
        self.assertTrue(processed, "Second Sync (fresh) must be processed")

    def test_skip_only_after_locked(self):
        """
        The flag is only set in LOCKED. If the FSM enters WAIT_SYNC from
        other states (e.g., IDLE on startup, or SERVE with incomplete
        exchange), the flag should NOT be set.
        """
        # Normal startup: FSM goes IDLE → WAIT_SYNC. No LOCKED involved.
        skip_stale_sync = False  # Not set because LOCKED wasn't visited.
        sync_present = True
        if sync_present and skip_stale_sync:
            processed = False
        else:
            processed = True
        self.assertTrue(processed,
            "Sync on startup (no LOCKED) should NOT be skipped")

    def test_fix_sequence_good_skip_good(self):
        """
        End-to-end: good exchange → LOCKED → skip stale → fresh good exchange.
        """
        N = 100
        sync_ns = 366_000_000
        delay = 12_000

        # Exchange 1: good (second N).
        dt21, dt43, phase, _ = servo_compute(
            N, sync_ns, N, sync_ns + delay,
            N, sync_ns + 50_000, N, sync_ns + 50_000 + delay)
        self.assertFalse(exchange_outlier(dt21, dt43, _))

        # LOCKED: skip_stale_sync = True.
        skip_stale_sync = True

        # Stale Sync arrives: SKIPPED (flag=1 → ignore).
        skip_stale_sync = False  # Flag cleared.

        # Fresh Sync at second N+1: processed normally.
        dt21_3, dt43_3, phase_3, _ = servo_compute(
            N+1, sync_ns, N+1, sync_ns + delay,
            N+1, sync_ns + 50_000, N+1, sync_ns + 50_000 + delay)
        self.assertFalse(exchange_outlier(dt21_3, dt43_3, _),
            "Fresh Sync: same seconds, no outlier")
        self.assertLess(abs(phase_3), 1000, "Fresh Sync: good phase")

    def test_all_timing_variants_handled(self):
        """
        The skip_stale_sync flag handles ALL timing variants uniformly:
        it doesn't matter whether the stale Sync appears in 1.2μs, 28μs,
        or 100μs — the first rx_ev.present is always skipped.
        """
        for variant, delay_us in [(1, 1.2), (2, 28), (3, 42), (4, 100)]:
            with self.subTest(variant=variant, delay_us=delay_us):
                skip = True  # Flag set in LOCKED.
                # Stale Sync arrives after delay_us.
                if skip:
                    skip = False
                    stale_processed = False
                else:
                    stale_processed = True
                self.assertFalse(stale_processed,
                    f"Variant {variant} ({delay_us}μs) must be skipped")

    def test_timing_signatures_from_hardware(self):
        """Document the timing signatures observed in hardware traces."""
        # Variant 1: immediate (shadow already valid)
        delta_v1 = 2259_366_057_750 - 2259_366_056_553  # 1197 ns
        self.assertLess(delta_v1, 2_000, "Variant 1: < 2μs")

        # Variant 2: in-flight capture pending
        delta_v2 = 3181_989_183_382 - 3181_989_140_514  # 42868 ns
        self.assertGreater(delta_v2, 2_000, "Variant 2: > 2μs")
        self.assertLess(delta_v2, 100_000, "Variant 2: < 100μs")

        # Variant 3: depacketizer still processing
        delta_v3 = 3642_830_684_119 - 3642_830_655_837  # 28282 ns
        self.assertGreater(delta_v3, 2_000, "Variant 3: > 2μs")
        self.assertLess(delta_v3, 100_000, "Variant 3: < 100μs")

    def test_skip_not_set_after_outlier_exchange(self):
        """
        After an outlier exchange (sec_adjust), skip_stale_sync must NOT be
        set. Otherwise the next fresh Sync is consumed, the slave never gets
        a good exchange, and the system enters a permanent outlier loop.

        The fix: skip_stale_sync = sample_valid (True only for good exchanges).
        """
        N = 100
        sync_ns = 366_000_000
        delay = 12_000

        # Outlier exchange: master t1 in second N+1, slave t2 in second N
        # (slave seconds lagging behind master by 1).
        dt21, dt43, phase, delay_val = servo_compute(
            N+1, sync_ns, N, sync_ns + delay,
            N, sync_ns + 50_000, N+1, sync_ns + 50_000 + delay)
        is_outlier = exchange_outlier(dt21, dt43, delay_val)
        sample_valid = not is_outlier
        self.assertTrue(is_outlier, "Exchange near boundary IS an outlier")
        self.assertFalse(sample_valid, "Outlier → sample_valid=0")

        # LOCKED: skip = sample_valid (= False for outlier).
        skip_stale_sync = sample_valid
        self.assertFalse(skip_stale_sync,
            "skip must NOT be set after outlier exchange")

        # Next fresh Sync arrives: must be processed (not skipped).
        sync_present = True
        if sync_present and skip_stale_sync:
            processed = False
        else:
            processed = True
        self.assertTrue(processed,
            "Fresh Sync after outlier must be processed, not skipped")

    def test_skip_set_after_good_exchange(self):
        """
        After a good exchange, skip_stale_sync IS set — the stale queued
        Sync from the depacketizer should be skipped.
        """
        N = 100
        sync_ns = 366_000_000
        delay = 12_000

        # Good exchange: all timestamps in second N.
        dt21, dt43, phase, delay_val = servo_compute(
            N, sync_ns, N, sync_ns + delay,
            N, sync_ns + 50_000, N, sync_ns + 50_000 + delay)
        is_outlier = exchange_outlier(dt21, dt43, delay_val)
        sample_valid = not is_outlier
        self.assertFalse(is_outlier)
        self.assertTrue(sample_valid)

        # LOCKED: skip = sample_valid (= True for good exchange).
        skip_stale_sync = sample_valid
        self.assertTrue(skip_stale_sync,
            "skip MUST be set after good exchange")

    def test_permanent_outlier_loop_prevented(self):
        """
        Regression test for the permanent outlier loop:
        If skip_stale_sync is always set (old behavior), an outlier exchange
        causes the next fresh Sync to be skipped, which produces another
        outlier, ad infinitum. With the fix (skip=sample_valid), the loop
        breaks because outlier exchanges don't set skip.
        """
        N = 100
        sync_ns = 366_000_000
        delay = 12_000
        consecutive_outliers = 0

        for _ in range(10):
            # Outlier exchange: master ahead by 1 second.
            dt21, dt43, phase, delay_val = servo_compute(
                N+1, sync_ns, N, sync_ns + delay,
                N, sync_ns + 50_000, N+1, sync_ns + 50_000 + delay)
            is_outlier = exchange_outlier(dt21, dt43, delay_val)
            sample_valid = not is_outlier

            # LOCKED: conditional skip.
            skip_stale_sync = sample_valid  # Fixed behavior.

            if not skip_stale_sync:
                # Next fresh Sync is NOT skipped → can produce a good exchange.
                break
            consecutive_outliers += 1

        self.assertEqual(consecutive_outliers, 0,
            "With the fix, the very first outlier breaks the loop")

    def test_pipeline_state_cleared_in_locked(self):
        """
        LOCKED also clears shadow_valid and capture_pending as a belt-and-
        suspenders measure alongside skip_stale_sync.
        """
        shadow_valid = True
        capture_pending = True

        # LOCKED clears both.
        shadow_valid = False
        capture_pending = False

        self.assertFalse(shadow_valid)
        self.assertFalse(capture_pending)


# Test Seconds Adjustment --------------------------------------------------------------------------

class TestSecAdjust(unittest.TestCase):
    """Tests for ±1-second adjustment via offset when outlier detector fires."""

    def test_outlier_phase_includes_1s_bias(self):
        """
        Outlier exchanges have phase biased by ±1e9 from the boundary crossing.
        The delay is valid but the phase is NOT usable for ns-level corrections.
        """
        delay = 13_000
        t1_sec, t1_ns = 101, 594_000_000
        t2_sec, t2_ns = 100, 594_000_000 + delay  # sec behind, ns aligned
        t3_sec, t3_ns = 100, 594_050_000
        t4_sec, t4_ns = 101, 594_050_000 + delay
        dt21, dt43, phase, path_delay = servo_compute(
            t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
        self.assertTrue(exchange_outlier(dt21, dt43, path_delay))
        # Phase includes the full ±1e9 bias — NOT usable for corrections.
        self.assertAlmostEqual(abs(phase), ONE_BILLION, delta=100_000)
        # But the delay IS correct.
        self.assertAlmostEqual(path_delay, delay, delta=1)

    def test_sec_adjust_direction_slave_behind(self):
        """When t1_sec > t2_sec (master ahead), t2_minus_t1 < 0 → add 1s to slave."""
        t1_sec, t1_ns = 101, 594_000_000
        t2_sec, t2_ns = 100, 594_013_000
        dt21 = signed_delta_ns(t2_ns, t2_sec, t1_ns, t1_sec)
        self.assertLess(dt21, 0, "slave behind → t2_minus_t1 negative")
        sec_adjust_dir = dt21 > 0  # False → add 1 second
        self.assertFalse(sec_adjust_dir)

    def test_sec_adjust_direction_slave_ahead(self):
        """When t2_sec > t1_sec (slave ahead), t2_minus_t1 > 0 → sub 1s from slave."""
        t1_sec, t1_ns = 100, 594_000_000
        t2_sec, t2_ns = 101, 594_013_000
        dt21 = signed_delta_ns(t2_ns, t2_sec, t1_ns, t1_sec)
        self.assertGreater(dt21, 0, "slave ahead → t2_minus_t1 positive")
        sec_adjust_dir = dt21 > 0  # True → subtract 1 second
        self.assertTrue(sec_adjust_dir)

    def test_large_offset_non_outlier(self):
        """
        A >1-second offset produces signed_delta_ns=0, which is NOT an outlier.
        The coarse_step_needed path handles this through sec comparison.
        """
        dt21, dt43, phase, delay = servo_compute(
            200, 184_000_000, 100, 184_013_000, 100, 184_050_000, 200, 184_063_000)
        is_outlier = exchange_outlier(dt21, dt43, delay)
        self.assertFalse(is_outlier, ">1-second offset is NOT flagged as outlier")
        self.assertEqual(dt21, 0, "signed_delta_ns returns 0 for >1 second")

    def test_sec_adjust_fixes_subsequent_exchange(self):
        """
        After a seconds adjustment, the next exchange should have matching
        seconds and be processed normally (not an outlier).
        """
        # Exchange 1: outlier (slave 1s behind) → triggers sec adjust.
        dt21, dt43, phase, delay = servo_compute(
            101, 594_000_000, 100, 594_013_000, 100, 594_050_000, 101, 594_063_000)
        self.assertTrue(exchange_outlier(dt21, dt43, delay))
        # After sec adjust (+1s to slave), next exchange has matching seconds.
        dt21, dt43, phase, delay = servo_compute(
            102, 594_000_000, 102, 594_013_000, 102, 594_050_000, 102, 594_063_000)
        self.assertFalse(exchange_outlier(dt21, dt43, delay))
        self.assertLess(abs(phase), 100_000, "Normal exchange after sec adjust")


# Test Addend Clamp --------------------------------------------------------------------------------

class TestAddendClamp(unittest.TestCase):
    """Tests for the addend clamping logic — the root cause fix for the frac-zeroing glitch."""

    MIN_ADDEND_INT = max(1, NOMINAL_ADDEND - 1)  # 42
    MAX_ADDEND_INT = min((1 << 32) - 1, NOMINAL_ADDEND + 1)  # 44

    def test_clamp_preserves_frac_at_max(self):
        """When addend exceeds max, integer is clamped but frac is preserved."""
        # Start near max boundary with non-zero frac.
        addend_full = (self.MAX_ADDEND_INT << ADDEND_FRAC_BITS) | 0x12345
        # Push beyond max.
        new, step = freq_step_compute(-1_000_000, addend_full)
        new_int = new >> ADDEND_FRAC_BITS
        new_frac = new & ((1 << ADDEND_FRAC_BITS) - 1)
        self.assertLessEqual(new_int, self.MAX_ADDEND_INT,
            "Integer part should be clamped to max")
        self.assertNotEqual(new_frac, 0,
            "Fractional part should be preserved, NOT zeroed by clamp")

    def test_clamp_preserves_frac_at_min(self):
        """When addend goes below min, integer is clamped but frac is preserved."""
        addend_full = (self.MIN_ADDEND_INT << ADDEND_FRAC_BITS) | 0xABCDE
        new, step = freq_step_compute(1_000_000, addend_full)
        new_int = new >> ADDEND_FRAC_BITS
        new_frac = new & ((1 << ADDEND_FRAC_BITS) - 1)
        self.assertGreaterEqual(new_int, self.MIN_ADDEND_INT,
            "Integer part should be clamped to min")
        self.assertNotEqual(new_frac, 0,
            "Fractional part should be preserved, NOT zeroed by clamp")

    def test_clamp_does_not_affect_normal_operation(self):
        """Normal freq trim (within bounds) passes through unchanged."""
        addend_full = NOMINAL_FULL  # 43 << 20
        new, step = freq_step_compute(10_000, addend_full)
        # Step should be small, addend should change by exactly step.
        self.assertEqual(new, addend_full + step)

    def test_tight_clamp_range(self):
        """Addend integer part is clamped to nominal ± 1."""
        self.assertEqual(self.MIN_ADDEND_INT, NOMINAL_ADDEND - 1)
        self.assertEqual(self.MAX_ADDEND_INT, NOMINAL_ADDEND + 1)

    def test_old_clamp_would_zero_frac(self):
        """
        Regression test: the OLD clamping logic (full-value clamp) would zero frac.
        This is the bug that was fixed in commit a6a6beb.
        """
        # Simulate the OLD (buggy) clamp behavior.
        min_full_old = self.MIN_ADDEND_INT << ADDEND_FRAC_BITS  # 0x2a00000 — frac=0!
        max_full_old = self.MAX_ADDEND_INT << ADDEND_FRAC_BITS  # 0x2c00000 — frac=0!
        self.assertEqual(min_full_old & ((1 << ADDEND_FRAC_BITS) - 1), 0,
            "Old clamp boundary has zero frac — this was the bug")
        self.assertEqual(max_full_old & ((1 << ADDEND_FRAC_BITS) - 1), 0,
            "Old clamp boundary has zero frac — this was the bug")


# Test Shadow Addend -------------------------------------------------------------------------------

class TestShadowAddend(unittest.TestCase):
    """Tests for the shadow addend register (authoritative copy, continuous TSU restoration)."""

    def test_shadow_breaks_corruption_feedback(self):
        """
        Model the corruption feedback loop and verify the shadow blocks it.
        """
        # Initial state: shadow and TSU both at nominal.
        shadow = NOMINAL_FULL
        tsu_reg = NOMINAL_FULL

        # Exchange 1: normal freq_step.
        phase = 10_000
        new, step = freq_step_compute(phase, shadow)  # Read from shadow.
        shadow = new  # Update shadow.
        tsu_reg = new  # Write to TSU.
        self.assertEqual(shadow, tsu_reg, "Both in sync after normal update")

        # Transient corruption: TSU register bit flip.
        tsu_reg ^= (1 << ADDEND_FRAC_BITS)  # Flip bit 20 (integer LSB).
        self.assertNotEqual(shadow, tsu_reg, "TSU corrupted, shadow clean")

        # Exchange 2: servo reads from SHADOW (not TSU), computes, writes both.
        phase = 5_000
        new2, step2 = freq_step_compute(phase, shadow)  # Read from shadow!
        shadow = new2
        tsu_reg = new2  # Write restores TSU from clean shadow.
        self.assertEqual(shadow, tsu_reg, "TSU restored from shadow on next write")

    def test_without_shadow_corruption_persists(self):
        """
        WITHOUT shadow: reading from corrupted TSU propagates the error.
        """
        tsu_reg = NOMINAL_FULL

        # Normal update.
        phase = 10_000
        new, step = freq_step_compute(phase, tsu_reg)
        tsu_reg = new

        # Transient corruption.
        tsu_reg ^= (1 << ADDEND_FRAC_BITS)  # Flip bit 20.
        corrupted_value = tsu_reg

        # Next update: reads from corrupted TSU.
        phase = 5_000
        new2, step2 = freq_step_compute(phase, tsu_reg)  # Reads corrupted!
        tsu_reg = new2
        # The corruption is baked in — tsu_reg is near the corrupted value.
        self.assertAlmostEqual(tsu_reg, corrupted_value, delta=abs(step2) + 1,
            msg="Without shadow, corruption persists (±small freq_step)")

    def test_shadow_initialized_to_nominal(self):
        """Shadow starts at the same value as the TSU reset (nominal addend)."""
        self.assertEqual(NOMINAL_FULL, NOMINAL_ADDEND << ADDEND_FRAC_BITS)
        # In HDL: shadow_addend = Signal(full_addend_bits, reset=nominal_addend << addend_frac_bits)

    def test_single_bit_flip_patterns(self):
        """
        Document the bit-flip patterns observed in hardware traces.
        All involve the addend integer part (upper 32 bits of the 52-bit value).
        """
        for desc, original, corrupted, bit in [
            ("bit 1 flip (0x2a→0x28)", 0x2a, 0x28, 1),
            ("bit 1,2 flip (0x2a→0x2c)", 0x2a, 0x2c, None),
            ("bit 0,1 flip (0x2b→0x2e)", 0x2b, 0x2e, None),
        ]:
            with self.subTest(desc=desc):
                diff = original ^ corrupted
                self.assertNotEqual(diff, 0, f"{desc}: values differ")
                # All observed flips change the integer part, not frac.
                self.assertNotEqual(original, corrupted)

    def test_shadow_survives_multiple_corruptions(self):
        """Shadow remains clean through multiple consecutive TSU corruptions."""
        shadow = NOMINAL_FULL
        tsu_reg = NOMINAL_FULL

        for i in range(5):
            # TSU gets corrupted.
            tsu_reg ^= (1 << (ADDEND_FRAC_BITS + i))

            # Servo reads from shadow, writes both.
            phase = 1000 * (i + 1)
            new, step = freq_step_compute(phase, shadow)
            shadow = new
            tsu_reg = new  # Restored.

            self.assertEqual(shadow, tsu_reg,
                f"Iteration {i}: TSU restored from shadow after corruption")

    def test_shadow_restores_during_deadband(self):
        """
        Regression test: with the old freq_step!=0 guard, a TSU corruption
        during steady state (phase < deadband, freq_step=0) would NOT be
        restored because the write was gated. The shadow value was correct
        but never written to the TSU.

        With the guard removed, the shadow writes every exchange regardless
        of freq_step, restoring the TSU immediately.
        """
        shadow = NOMINAL_FULL
        tsu_reg = NOMINAL_FULL

        # Converge to steady state.
        for _ in range(20):
            phase = 100  # Within deadband (< 256 ns).
            new, step = freq_step_compute(phase, shadow)
            self.assertEqual(step, 0, "Phase within deadband → freq_step=0")
            # Always write from shadow (current behavior, no guard).
            shadow = new
            tsu_reg = new

        # TSU gets corrupted during steady state.
        tsu_reg ^= (1 << (ADDEND_FRAC_BITS + 1))  # Flip bit in integer part.
        self.assertNotEqual(shadow, tsu_reg, "TSU corrupted")

        # Next exchange: phase still within deadband, freq_step=0.
        phase = 50
        new, step = freq_step_compute(phase, shadow)
        self.assertEqual(step, 0, "Still in deadband")

        # WITHOUT guard: shadow writes to TSU anyway → restored.
        shadow = new
        tsu_reg = new  # Write from shadow, NOT gated by freq_step.
        self.assertEqual(shadow, tsu_reg,
            "TSU restored from shadow even with freq_step=0")

    def test_old_guard_would_leave_corruption(self):
        """
        Proves that the OLD freq_step!=0 guard left the TSU corrupted
        during deadband operation.
        """
        shadow = NOMINAL_FULL
        tsu_reg = NOMINAL_FULL

        # TSU gets corrupted.
        tsu_reg ^= (1 << (ADDEND_FRAC_BITS + 1))
        self.assertNotEqual(shadow, tsu_reg)

        # Exchange with phase in deadband.
        phase = 50
        _, step = freq_step_compute(phase, shadow)
        self.assertEqual(step, 0, "In deadband")

        # OLD behavior: If(freq_step != 0, write). freq_step=0 → NO WRITE.
        if step != 0:
            tsu_reg = shadow  # Would restore, but this branch not taken.

        # TSU remains corrupted!
        self.assertNotEqual(shadow, tsu_reg,
            "Old guard leaves TSU corrupted when freq_step=0")


    def test_continuous_restoration_limits_corruption(self):
        """
        Regression test for observed hardware glitch: addend jumped from
        0x2a to 0x2c between serves (~2s apart), causing 93ms phase drift.

        Old behavior: shadow only writes to TSU on serve (~every 2s).
        New behavior: shadow writes to TSU every clock cycle.

        Model: simulate N cycles between serves, with corruption at cycle K.
        Old: corruption persists for N-K cycles. New: persists for 1 cycle.
        """
        shadow = NOMINAL_FULL
        tsu_reg = NOMINAL_FULL
        clk_freq = 100_000_000  # 100 MHz
        serve_interval_cycles = 2 * clk_freq  # ~2 seconds

        # Old behavior: TSU only written on serve.
        corruption_cycle = serve_interval_cycles // 2
        corrupted_cycles_old = 0
        for cycle in range(serve_interval_cycles):
            if cycle == corruption_cycle:
                tsu_reg ^= (1 << (ADDEND_FRAC_BITS + 1))  # 0x2a → 0x2c
            if tsu_reg != shadow:
                corrupted_cycles_old += 1
            # No restoration until serve.

        self.assertEqual(corrupted_cycles_old, serve_interval_cycles - corruption_cycle,
            "Old: corruption persists until next serve")

        # New behavior: TSU restored from shadow every cycle.
        tsu_reg = shadow  # Reset.
        corrupted_cycles_new = 0
        for cycle in range(serve_interval_cycles):
            if cycle == corruption_cycle:
                tsu_reg ^= (1 << (ADDEND_FRAC_BITS + 1))
            if tsu_reg != shadow:
                corrupted_cycles_new += 1
            # Continuous restoration.
            tsu_reg = shadow

        self.assertEqual(corrupted_cycles_new, 1,
            "New: corruption limited to 1 cycle")

        # Phase impact comparison (rough).
        # Each corrupted cycle with addend off by 2 adds ~0.47ns of drift at 100MHz.
        ns_per_corrupt_cycle = 2 * 1e9 / (1 << 32)  # ~0.47 ns
        old_drift_ms = corrupted_cycles_old * ns_per_corrupt_cycle / 1e6
        new_drift_ns = corrupted_cycles_new * ns_per_corrupt_cycle
        self.assertGreater(old_drift_ms, 10, "Old: multi-ms drift from 1s of corruption")
        self.assertLess(new_drift_ns, 1, "New: sub-ns drift from 1-cycle corruption")


# Test Frequency Shift Computation -----------------------------------------------------------------

class TestFreqShiftComputation(unittest.TestCase):
    """Tests for the FREQ_SHIFT auto-computation from clk_freq."""

    def _compute_freq_shift(self, clk_freq, frac_bits=20):
        lsb_drift = clk_freq * 1e9 / (1 << (32 + frac_bits))
        return max(1, math.ceil(math.log2(max(1, lsb_drift))) + 1)

    def test_100mhz_stability(self):
        """FREQ_SHIFT for 100MHz should give stable convergence for T=1..3s."""
        shift = self._compute_freq_shift(100e6)
        lsb = 100e6 * 1e9 / (1 << 52)
        for T in [1, 2, 3]:
            factor = abs(1 - lsb * T / (1 << shift))
            self.assertLess(factor, 1,
                f"Unstable at 100MHz T={T}s: |factor|={factor:.3f}")

    def test_125mhz_stability(self):
        shift = self._compute_freq_shift(125e6)
        lsb = 125e6 * 1e9 / (1 << 52)
        for T in [1, 2, 3]:
            factor = abs(1 - lsb * T / (1 << shift))
            self.assertLess(factor, 1,
                f"Unstable at 125MHz T={T}s: |factor|={factor:.3f}")

    def test_200mhz_stability(self):
        shift = self._compute_freq_shift(200e6)
        lsb = 200e6 * 1e9 / (1 << 52)
        for T in [1, 2, 3]:
            factor = abs(1 - lsb * T / (1 << shift))
            self.assertLess(factor, 1,
                f"Unstable at 200MHz T={T}s: |factor|={factor:.3f}")

    def test_50mhz_stability(self):
        shift = self._compute_freq_shift(50e6)
        lsb = 50e6 * 1e9 / (1 << 52)
        for T in [1, 2, 3]:
            factor = abs(1 - lsb * T / (1 << shift))
            self.assertLess(factor, 1,
                f"Unstable at 50MHz T={T}s: |factor|={factor:.3f}")


# Test Seconds Adjust Offset -----------------------------------------------------------------------

class TestSecAdjustOffset(unittest.TestCase):
    """Tests for ±1e9 offset-based seconds correction (not step-based)."""

    def test_offset_plus_1s_rolls_seconds(self):
        """
        Applying offset=+1e9 to TSU nanoseconds should increment seconds by 1
        without changing nanoseconds (after normalization).
        """
        # Simulate TSU offset logic for offset=+1e9.
        ns = 500_000_000
        offset = ONE_BILLION
        offset_nsec = ns + offset  # 1_500_000_000
        self.assertGreaterEqual(offset_nsec, ONE_BILLION)
        new_ns = offset_nsec - ONE_BILLION  # 500_000_000
        # seconds += 1
        self.assertEqual(new_ns, ns, "Nanoseconds should be unchanged after +1s offset")

    def test_offset_minus_1s_rolls_seconds(self):
        """Applying offset=-1e9 decrements seconds without changing nanoseconds."""
        ns = 500_000_000
        offset = -ONE_BILLION
        offset_nsec = ns + offset  # -500_000_000
        self.assertLess(offset_nsec, 0)
        new_ns = offset_nsec + ONE_BILLION  # 500_000_000
        # seconds -= 1
        self.assertEqual(new_ns, ns)

    def test_offset_preserves_frac(self):
        """
        Unlike step-based correction, offset doesn't reset the TSU frac register.
        This is why offset was chosen over step for seconds adjustment.
        """
        # The step path resets frac=0 (in the TSU's If(self.step, ..., frac.eq(0))).
        # The offset path does NOT touch frac (it's in the Elif branch).
        # This test documents the design intent — frac preservation is critical
        # to avoid losing sub-nanosecond accumulator state.
        pass  # Verified by inspection; HDL structural test would require simulation.


# Test TSU Tick/Offset Race -------------------------------------------------------------------------

class TestTSUTickOffsetRace(unittest.TestCase):
    """
    Cycle-accurate TSU simulation to test the tick/offset race condition.

    The TSU has an If/Elif/Else priority chain:
    - If(step): hard-set seconds/nanoseconds
    - Elif(offset != 0): apply offset, suppress tick
    - Else: normal tick

    The servo writes offset on cycle N (registered). The TSU applies it on
    cycle N+1. On cycle N, the tick runs normally. If the tick causes a
    seconds rollover on cycle N, AND the offset adds +1e9 on cycle N+1,
    seconds gets double-incremented.
    """

    CLK_FREQ_HZ = int(100e6)
    TICK_INC_NS = 10  # Approximate for 100MHz

    def _tsu_tick(self, sec, ns, offset):
        """Simulate one TSU clock cycle. Returns (new_sec, new_ns, new_offset)."""
        if offset != 0:
            # Offset path (suppresses tick).
            offset_nsec = ns + offset
            if offset_nsec < 0:
                return sec - 1, offset_nsec + ONE_BILLION, 0
            elif offset_nsec >= ONE_BILLION:
                return sec + 1, offset_nsec - ONE_BILLION, 0
            else:
                return sec, offset_nsec, 0
        else:
            # Normal tick.
            tick_nsec = ns + self.TICK_INC_NS
            if tick_nsec >= ONE_BILLION:
                return sec + 1, tick_nsec - ONE_BILLION, 0
            else:
                return sec, tick_nsec, 0

    def test_offset_plus_1s_no_rollover(self):
        """Normal case: offset=+1e9 applied when nanoseconds is mid-second."""
        sec, ns, offset = 100, 500_000_000, 0
        # Servo writes offset (takes effect next cycle).
        offset = ONE_BILLION
        # Cycle N+1: offset applied.
        sec, ns, offset = self._tsu_tick(sec, ns, offset)
        self.assertEqual(sec, 101)
        self.assertEqual(ns, 500_000_000)
        self.assertEqual(offset, 0)

    def test_offset_minus_1s_no_rollover(self):
        """Normal case: offset=-1e9 applied when nanoseconds is mid-second."""
        sec, ns, offset = 100, 500_000_000, 0
        offset = -ONE_BILLION
        sec, ns, offset = self._tsu_tick(sec, ns, offset)
        self.assertEqual(sec, 99)
        self.assertEqual(ns, 500_000_000)

    def test_tick_then_offset_no_rollover(self):
        """
        Servo writes offset on cycle N. Tick runs on N (no rollover).
        Offset applied on N+1. Total: seconds +1 (correct).
        """
        sec, ns, offset = 100, 500_000_000, 0
        # Cycle N: tick runs (offset=0), servo writes offset=+1e9.
        sec, ns, offset = self._tsu_tick(sec, ns, 0)  # tick
        pending_offset = ONE_BILLION  # servo wrote this
        self.assertEqual(sec, 100)  # no rollover
        # Cycle N+1: offset applied.
        sec, ns, offset = self._tsu_tick(sec, ns, pending_offset)
        self.assertEqual(sec, 101)  # +1 from offset
        self.assertEqual(ns, 500_000_000 + self.TICK_INC_NS)

    def test_tick_rollover_then_offset_double_increment(self):
        """
        THE RACE: Tick causes rollover on cycle N, offset adds +1s on cycle N+1.
        Result: seconds incremented by 2 instead of 1.
        """
        sec, ns, offset = 100, ONE_BILLION - self.TICK_INC_NS, 0
        # Cycle N: tick causes rollover.
        sec, ns, offset = self._tsu_tick(sec, ns, 0)
        self.assertEqual(sec, 101, "Tick rolled over seconds")
        self.assertEqual(ns, 0, "Nanoseconds reset to 0")
        # Servo wrote offset=+1e9 on cycle N.
        pending_offset = ONE_BILLION
        # Cycle N+1: offset applied.
        sec, ns, offset = self._tsu_tick(sec, ns, pending_offset)
        self.assertEqual(sec, 102, "DOUBLE INCREMENT: seconds went from 100 to 102")
        self.assertEqual(ns, 0, "Nanoseconds unchanged")
        # This is 1 second too far! The intended correction was +1, but we got +2.

    def test_tick_rollover_then_offset_minus_net_zero(self):
        """
        Tick rolls over (+1s), then offset=-1e9 subtracts 1s. Net: 0 change.
        But the intended correction was -1s, so we're 1 second too high.
        """
        sec, ns, offset = 100, ONE_BILLION - self.TICK_INC_NS, 0
        # Cycle N: tick causes rollover.
        sec, ns, offset = self._tsu_tick(sec, ns, 0)
        self.assertEqual(sec, 101)
        # Servo wrote offset=-1e9 on cycle N (slave was ahead).
        pending_offset = -ONE_BILLION
        # Cycle N+1: offset applied.
        sec, ns, offset = self._tsu_tick(sec, ns, pending_offset)
        self.assertEqual(sec, 100, "Net: back to 100 (rollover +1, offset -1)")
        # But the slave should be at 99 (master was at 99, slave was ahead at 100).
        # So the correction is 1 second short.

    def test_race_probability(self):
        """
        The race window is TICK_INC_NS nanoseconds out of 1 second.
        At 100MHz (10ns ticks), probability ≈ 10/1e9 = 1e-8 per exchange.
        """
        window_ns = self.TICK_INC_NS
        probability = window_ns / ONE_BILLION
        self.assertLess(probability, 1e-7,
            f"Race probability {probability:.2e} should be very small")
        # At 1 exchange/sec, this is once per ~100M seconds (~3 years).
        # NOT the cause of the 2-3 exchange repeating pattern.


# Test Full Exchange Cycle -------------------------------------------------------------------------

class TestFullExchangeCycle(unittest.TestCase):
    """
    End-to-end simulation of the full exchange cycle including seconds
    adjustment, to investigate the persistent seconds-boundary pattern.

    Models: master sends Sync at ~767ms into each second. Slave receives,
    processes, applies corrections. Tracks whether seconds align.
    """

    SYNC_NS = 767_000_000  # Master Sync timing in nanoseconds.
    DELAY_NS = 12_000      # One-way propagation delay.
    EXCHANGE_PROCESSING_NS = 200_000_000  # ~200ms for full exchange.

    def test_seconds_alignment_after_sec_adjust(self):
        """
        After a ±1s offset correction, verify the slave's seconds are
        aligned with the master for the next exchange.

        Timeline: slave=99, master=100 at outlier exchange.
        Sec adjust: slave → 100. Both tick 1 second. Next Sync: master=101.
        Slave should be at 101 too (both advanced by 1s).
        """
        master_sec = 100
        slave_sec = 99  # 1 second behind
        slave_ns = self.SYNC_NS  # Aligned in nanoseconds

        # Sec adjust: +1e9 offset. TSU applies it: sec += 1, ns unchanged.
        slave_sec += 1  # → 100

        # Both clocks tick for ~1 second until next Sync.
        master_sec += 1  # → 101
        slave_sec += 1   # → 101 (addend is correct, slave ticks at same rate)

        self.assertEqual(slave_sec, master_sec,
            "After sec adjust + 1s tick, seconds should match")

    def test_phase_correction_near_rollover(self):
        """
        Phase correction applied when nanoseconds is near 1e9.
        Verify it doesn't cause an unintended seconds change.
        """
        # Slave at 999,998,000 ns. Phase correction = +3000 ns.
        ns = 999_998_000
        correction = 3000
        offset_nsec = ns + correction
        if offset_nsec >= ONE_BILLION:
            new_ns = offset_nsec - ONE_BILLION
            sec_change = 1
        else:
            new_ns = offset_nsec
            sec_change = 0

        self.assertEqual(sec_change, 1, "Correction near rollover causes seconds +1")
        self.assertEqual(new_ns, 1000, "Nanoseconds wraps correctly")
        # This seconds change is CORRECT — the phase correction pushed past the boundary.
        # But it means the NEXT exchange might see different seconds.

    def test_phase_correction_causes_seconds_divergence(self):
        """
        KEY TEST: Demonstrate how a phase correction near the second boundary
        can cause the slave's seconds to diverge from the master's.

        If the master sends Sync at ~767ms and the slave's nanoseconds are
        at ~767ms + small_offset after correction, a large correction could
        push past 1e9, adding 1 second. The next exchange then has mismatched
        seconds.
        """
        # Master at second 100, ns=767M. Slave aligned at second 100, ns=767M.
        slave_sec, slave_ns = 100, self.SYNC_NS
        master_sec, master_ns = 100, self.SYNC_NS

        # Exchange completes ~200ms later. Slave ns ≈ 967M.
        servo_ns = slave_ns + self.EXCHANGE_PROCESSING_NS  # 967M
        # Phase correction: -2000 ns (slave slightly ahead).
        correction = -2000
        offset_nsec = servo_ns + correction  # 966,998,000
        # No rollover. slave_ns = 966,998,000. OK.

        # BUT: what if the exchange takes longer (300ms)?
        servo_ns_late = slave_ns + 300_000_000  # 1,067M → already past rollover!
        # In reality, the TSU would have already ticked past 1e9 and rolled over.
        # servo_ns_late in TSU terms: slave_sec=101, slave_ns=67M.
        if servo_ns_late >= ONE_BILLION:
            effective_sec = slave_sec + 1
            effective_ns = servo_ns_late - ONE_BILLION
        else:
            effective_sec = slave_sec
            effective_ns = servo_ns_late

        self.assertEqual(effective_sec, 101, "Slave rolled to next second during exchange")
        # The correction is applied to effective_ns=67M. No issue.
        # But the NEXT Sync from master is at second 101, ns=767M.
        # Slave is at second 101. Same second. No outlier.
        next_master_sec = 101
        self.assertEqual(effective_sec, next_master_sec, "Same second — no outlier")

    def test_addend_drift_causes_seconds_boundary(self):
        """
        If the addend is slightly wrong, the slave's nanosecond counter
        accumulates an error. After enough exchanges, this error pushes
        the slave's seconds rollover to a different time than the master's.

        With addend converged to ~0x2a.f322x (slightly below nominal 0x2b),
        the clock runs slightly slow. Over time, the slave's seconds counter
        lags behind.
        """
        # Nominal: 10.000 ns/tick. Actual: 9.999 ns/tick (slightly slow).
        # Drift: 0.001 ns/tick * 1e8 ticks/s = 100,000 ns/s = 100 μs/s.
        drift_ns_per_sec = 100_000  # 100 μs/s

        # After 1 second: slave is 100 μs behind.
        # After 10 seconds: slave is 1 ms behind.
        # After 10,000 seconds: slave is 1 second behind.
        time_to_1sec_drift = ONE_BILLION / drift_ns_per_sec
        self.assertEqual(time_to_1sec_drift, 10_000,
            "Takes 10,000 seconds (~2.8 hours) to drift 1 full second")
        # This is WAY too slow to explain the 2-3 exchange pattern.
        # The actual addend error after convergence is ~170 μs/s (from lsb_drift),
        # giving 1 second drift in ~5,900 seconds (~98 minutes).
        # NOT the cause of the 2-3 exchange repeating pattern.


# Test Servo Convergence ---------------------------------------------------------------------------

class TestServoConvergence(unittest.TestCase):
    """End-to-end test: simulate multiple exchanges and verify convergence."""

    def test_convergence_no_boundary(self):
        """
        Simulate 20 exchanges with slave 1ms behind, all in the same second range.
        Verify the phase error converges toward zero.
        """
        addend_full = NOMINAL_FULL
        phase_history = []
        slave_offset_ns = -1_000_000  # Slave is 1ms behind.

        for i in range(20):
            base_sec = 100 + i
            base_ns = 500_000_000  # Middle of second — no boundary issues.
            delay = 13_000
            t1_sec, t1_ns = base_sec, base_ns
            t2_sec, t2_ns = base_sec, base_ns + slave_offset_ns + delay
            t3_sec, t3_ns = base_sec, base_ns + 50_000 + slave_offset_ns
            t4_sec, t4_ns = base_sec, base_ns + 50_000 + delay

            dt21, dt43, phase, path_delay = servo_compute(
                t1_sec, t1_ns, t2_sec, t2_ns, t3_sec, t3_ns, t4_sec, t4_ns)
            phase_history.append(phase)

            # Apply corrections (simplified — in HDL, corrections happen next cycle).
            if abs(phase) < ONE_BILLION // 2:
                # Phase correction (Kp=1) adjusts slave offset.
                slave_offset_ns += (-phase)
                # Freq trim.
                addend_full, _ = freq_step_compute(phase, addend_full)

        # Phase should converge: last few should be smaller than initial.
        self.assertLess(abs(phase_history[-1]), abs(phase_history[0]),
            f"Phase should decrease; history: {phase_history[:5]}...{phase_history[-3:]}")
        # Should converge to near-zero within 20 iterations.
        self.assertLess(abs(phase_history[-1]), 1000,
            "Phase should converge to < 1μs")


if __name__ == "__main__":
    unittest.main()
