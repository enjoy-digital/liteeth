#
# This file is part of LiteEth.
#
# Copyright (c) 2026 luanvt
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from liteeth.common import *
from liteeth.mac import rate_limiter as rate_limiter_mod

from litex.gen.sim import *

VCD_NAME = "sim.vcd"

# Simulated link: dw=8, eth_tx at 10 MHz -> 80 Mbps wire.
# 10 Mbps shaped rate: 10/80 = 1/8 of line rate.
# Q16.16: 65536 / 8 = 8192
RATE_10MBPS = 8192

# Simulation clock periods in ns.
SYS_CLK_PERIOD_NS    = 1000  # 1 MHz
ETH_TX_CLK_PERIOD_NS = 100   # 10 MHz
ETH_TX_CYCLES_PER_SYS = SYS_CLK_PERIOD_NS // ETH_TX_CLK_PERIOD_NS

# Delay long enough for sys-domain CSR writes to cross and commit in eth_tx.
CSR_COMMIT_DELAY_SYS_CYCLES = 24
SENDER_START_DELAY_ETH_CYCLES = CSR_COMMIT_DELAY_SYS_CYCLES * ETH_TX_CYCLES_PER_SYS + 80

# Tolerance in cycles for timing assertions (covers FSM transition + CDC latency).
TIMING_TOLERANCE = 10


def expected_refill_cycles(charge_bytes, rate):
    """Cycles needed to refill charge_bytes at the given Q16.16 rate."""
    return -(-(charge_bytes << 16) // rate)


def make_frame(length, seed=0):
    return [((seed + i) % 256) for i in range(length)]


def wait_for_frames(dut, count, limit):
    for _ in range(limit):
        if len(dut.monitor.frames) >= count:
            return
        yield
    raise TimeoutError(f"Timed out waiting for {count} frame(s)")


def send_frame(sink, frame):
    for n, byte in enumerate(frame):
        yield sink.valid.eq(1)
        yield sink.data.eq(byte)
        yield sink.last.eq(n == (len(frame) - 1))
        yield sink.last_be.eq(n == (len(frame) - 1))
        while True:
            yield
            if (yield sink.ready):
                break

    yield sink.valid.eq(0)
    yield sink.data.eq(0)
    yield sink.last.eq(0)
    yield sink.last_be.eq(0)
    yield


def send_frame_with_stall_count(sink, frame):
    stall_cycles = 0

    for n, byte in enumerate(frame):
        yield sink.valid.eq(1)
        yield sink.data.eq(byte)
        yield sink.last.eq(n == (len(frame) - 1))
        yield sink.last_be.eq(n == (len(frame) - 1))
        while True:
            yield
            if (yield sink.ready):
                break
            stall_cycles += 1

    yield sink.valid.eq(0)
    yield sink.data.eq(0)
    yield sink.last.eq(0)
    yield sink.last_be.eq(0)
    yield

    return stall_cycles


class TXMonitor:
    def __init__(self, endpoint, dw):
        self.endpoint = endpoint
        self.dw       = dw
        self.frames   = []
        self.starts   = []
        self.gaps     = []

    def _beat_bytes(self, last, last_be):
        if not last:
            return self.dw // 8
        for byte in range(self.dw // 8):
            if last_be == (1 << byte):
                return byte + 1
        return self.dw // 8

    @passive
    def generator(self):
        cycle          = 0
        current_frame  = []
        previous_cycle = None

        while True:
            yield self.endpoint.ready.eq(1)
            if (yield self.endpoint.valid) and (yield self.endpoint.ready):
                data    = (yield self.endpoint.data)
                last    = (yield self.endpoint.last)
                last_be = (yield self.endpoint.last_be)

                if not current_frame:
                    self.starts.append(cycle)
                elif cycle != (previous_cycle + 1):
                    self.gaps.append((len(self.frames), previous_cycle, cycle))

                for byte in range(self._beat_bytes(last, last_be)):
                    current_frame.append((data >> (8*byte)) & 0xff)

                previous_cycle = cycle

                if last:
                    self.frames.append(current_frame)
                    current_frame  = []
                    previous_cycle = None

            cycle += 1
            yield


class DUT(LiteXModule):
    def __init__(self, rate=0, burst=None):
        self.limiter = rate_limiter_mod.LiteEthMACTokenBucket(
            dw    = 8,
            rate  = rate,
            burst = burst,
        )
        self.monitor = TXMonitor(self.limiter.source, 8)


def run_dut(dut, eth_tx_generator):
    generators = {
        "sys":    [],
        "eth_tx": [eth_tx_generator, dut.monitor.generator()],
    }
    clocks = {
        "sys":    SYS_CLK_PERIOD_NS,
        "eth_tx": ETH_TX_CLK_PERIOD_NS,
    }
    run_simulation(dut, generators, clocks, vcd_name=VCD_NAME)


def run_dut_with_generators(dut, sys_generators, eth_tx_generators):
    generators = {
        "sys":    sys_generators,
        "eth_tx": eth_tx_generators + [dut.monitor.generator()],
    }
    clocks = {
        "sys":    SYS_CLK_PERIOD_NS,
        "eth_tx": ETH_TX_CLK_PERIOD_NS,
    }
    run_simulation(dut, generators, clocks, vcd_name=VCD_NAME)


class TestMACRateLimiter(unittest.TestCase):
    def test_disabled_bypass(self):
        """Disabled limiter passes all frames transparently."""
        short_frame = make_frame(64, seed=0x10)
        long_frame  = make_frame(1500, seed=0x80)
        dut = DUT()

        def sender():
            yield from send_frame(dut.limiter.sink, short_frame)
            yield from send_frame(dut.limiter.sink, long_frame)
            yield from wait_for_frames(dut, 2, 4096)

        run_dut(dut, sender())

        self.assertEqual(dut.monitor.frames, [short_frame, long_frame])
        self.assertEqual(dut.monitor.gaps, [])

    def test_steady_state_pacing(self):
        """Single-frame burst at 10 Mbps — every frame after the first is paced."""
        frame_len    = eth_mtu                               # 1530 bytes on stream = charge
        frame_charge = eth_mtu                               # 1530 bytes charged
        n_frames     = 4
        frames       = [make_frame(frame_len, seed=i) for i in range(n_frames)]
        dut          = DUT(rate=RATE_10MBPS, burst=frame_charge)
        stall_counts = []

        refill = expected_refill_cycles(frame_charge, RATE_10MBPS)

        def sender():
            # Wait for sys-domain CSR writes to cross and commit in eth_tx.
            for _ in range(SENDER_START_DELAY_ETH_CYCLES):
                yield
            for f in frames:
                stall_counts.append(
                    (yield from send_frame_with_stall_count(dut.limiter.sink, f)))

        def control():
            yield dut.limiter._rate.storage.eq(RATE_10MBPS)
            yield dut.limiter._burst.storage.eq(frame_charge)
            for _ in range(CSR_COMMIT_DELAY_SYS_CYCLES):
                yield
            yield dut.limiter._enable.storage.eq(1)

        def waiter():
            yield from wait_for_frames(dut, n_frames, n_frames * (refill + frame_len) + 512)

        run_dut_with_generators(dut, [control()], [sender(), waiter()])

        # Data integrity — all frames received intact, no mid-frame bubbles.
        self.assertEqual(len(dut.monitor.frames), n_frames)
        for tx, expected in zip(dut.monitor.frames, frames):
            self.assertEqual(tx, expected)
        self.assertEqual(dut.monitor.gaps, [])

        # Frame 1 passes immediately from the burst budget.
        self.assertEqual(stall_counts[0], 0)

        # Subsequent frames must wait for 10 Mbps refill.
        for i in range(1, n_frames):
            self.assertAlmostEqual(stall_counts[i], refill,
                delta=frame_len + TIMING_TOLERANCE,
                msg=f"Frame {i} stall {stall_counts[i]} outside ±{frame_len + TIMING_TOLERANCE} of {refill}")

        # Uniform steady-state spacing.
        spacings = [dut.monitor.starts[i+1] - dut.monitor.starts[i]
                    for i in range(1, n_frames - 1)]
        for j, s in enumerate(spacings[1:], 2):
            self.assertAlmostEqual(s, spacings[0], delta=1,
                msg=f"Spacing[{j}]={s} differs from spacing[1]={spacings[0]}")

    def test_burst_then_pacing(self):
        """Burst budget absorbs initial spike, then paces at 10 Mbps."""
        rng          = random.Random(0xB057)
        # Randomize packet length near MTU to keep burst/pacing assertions stable.
        frame_len    = rng.randint(max(64, eth_mtu - 64), eth_mtu)
        frame_charge = frame_len
        n_burst      = 3
        n_total      = 6
        frames       = [make_frame(frame_len, seed=i*3) for i in range(n_total)]
        dut          = DUT(rate=RATE_10MBPS, burst=n_burst * frame_charge)
        stall_counts = []

        refill = expected_refill_cycles(frame_charge, RATE_10MBPS)

        def sender():
            for _ in range(SENDER_START_DELAY_ETH_CYCLES):
                yield
            for f in frames:
                stall_counts.append(
                    (yield from send_frame_with_stall_count(dut.limiter.sink, f)))

        def control():
            yield dut.limiter._rate.storage.eq(RATE_10MBPS)
            yield dut.limiter._burst.storage.eq(n_burst * frame_charge)
            # Let sys-domain CSR writes cross and commit before enabling shaping.
            for _ in range(CSR_COMMIT_DELAY_SYS_CYCLES):
                yield
            yield dut.limiter._enable.storage.eq(1)

        def waiter():
            yield from wait_for_frames(dut, n_total, n_total * (refill + frame_len) + 512)

        run_dut_with_generators(dut, [control()], [sender(), waiter()])

        self.assertEqual(len(dut.monitor.frames), n_total)
        for tx, expected in zip(dut.monitor.frames, frames):
            self.assertEqual(tx, expected)
        self.assertEqual(dut.monitor.gaps, [])

        # First n_burst frames pass without stalls (covered by burst budget).
        for i in range(n_burst):
            self.assertEqual(stall_counts[i], 0,
                msg=f"Burst frame {i} stalled ({stall_counts[i]} cycles)")

        # Frames beyond the burst must wait for 10 Mbps refill.
        # Stall is slightly less than `refill` because the bucket also
        # accumulates tokens during frame transmission itself.
        for i in range(n_burst, n_total):
            self.assertGreater(stall_counts[i], refill // 2,
                msg=f"Post-burst frame {i}: stall {stall_counts[i]} too short (expected ~{refill})")
            self.assertAlmostEqual(stall_counts[i], refill,
                delta=frame_len * 3,
                msg=f"Post-burst frame {i}: stall {stall_counts[i]} outside ±{frame_len*3} of {refill}")

    def test_mixed_frame_sizes(self):
        """Smaller frames charge less — shorter gaps at 10 Mbps."""
        small_len  = 64
        large_len  = eth_mtu  # 1530
        small_charge = small_len
        large_charge = large_len

        refill_small = expected_refill_cycles(small_charge, RATE_10MBPS)
        refill_large = expected_refill_cycles(large_charge, RATE_10MBPS)

        small = make_frame(small_len, seed=0xAA)
        large = make_frame(large_len, seed=0xCC)
        # Sequence: small → large → small → large
        frames = [small, large, small, large]
        dut    = DUT(rate=RATE_10MBPS, burst=large_charge)

        def sender():
            for _ in range(SENDER_START_DELAY_ETH_CYCLES):
                yield
            for f in frames:
                yield from send_frame(dut.limiter.sink, f)

        def control():
            yield dut.limiter._rate.storage.eq(RATE_10MBPS)
            yield dut.limiter._burst.storage.eq(large_charge)
            for _ in range(CSR_COMMIT_DELAY_SYS_CYCLES):
                yield
            yield dut.limiter._enable.storage.eq(1)

        def waiter():
            yield from wait_for_frames(dut, len(frames), len(frames) * (refill_large + large_len) + 512)

        run_dut_with_generators(dut, [control()], [sender(), waiter()])

        self.assertEqual(len(dut.monitor.frames), len(frames))
        for tx, expected in zip(dut.monitor.frames, frames):
            self.assertEqual(tx, expected)
        self.assertEqual(dut.monitor.gaps, [])

        spacings = [dut.monitor.starts[i+1] - dut.monitor.starts[i]
                    for i in range(len(frames) - 1)]

        # Gap after a small frame (charge 64) should be shorter than after a large frame (charge 1530).
        spacing_after_small_0 = spacings[0]  # after 64-byte frame
        spacing_after_large_0 = spacings[1]  # after 1530-byte frame
        self.assertLess(spacing_after_small_0, spacing_after_large_0,
            msg=f"Gap after small ({spacing_after_small_0}) should be < gap after large ({spacing_after_large_0})")

    def test_slow_sender_no_stall(self):
        """Sender slower than 10 Mbps — bucket refills fully, no stalls."""
        frame_len    = eth_mtu
        frame_charge = eth_mtu
        n_frames     = 4
        refill       = expected_refill_cycles(frame_charge, RATE_10MBPS)
        # Sender idles well beyond one refill period between frames.
        sender_gap   = refill + 500
        frames       = [make_frame(frame_len, seed=i*7) for i in range(n_frames)]
        dut          = DUT(rate=RATE_10MBPS, burst=frame_charge)
        stall_counts = []

        def sender():
            for _ in range(SENDER_START_DELAY_ETH_CYCLES):
                yield
            for f in frames:
                stall_counts.append(
                    (yield from send_frame_with_stall_count(dut.limiter.sink, f)))
                for _ in range(sender_gap):
                    yield

        def control():
            yield dut.limiter._rate.storage.eq(RATE_10MBPS)
            yield dut.limiter._burst.storage.eq(frame_charge)
            for _ in range(CSR_COMMIT_DELAY_SYS_CYCLES):
                yield
            yield dut.limiter._enable.storage.eq(1)

        def waiter():
            yield from wait_for_frames(dut, n_frames, n_frames * (sender_gap + frame_len) + 512)

        run_dut_with_generators(dut, [control()], [sender(), waiter()])

        self.assertEqual(len(dut.monitor.frames), n_frames)
        for tx, expected in zip(dut.monitor.frames, frames):
            self.assertEqual(tx, expected)
        self.assertEqual(dut.monitor.gaps, [])

        # Every frame should pass without stalls since sender is slower than 10 Mbps.
        for i, sc in enumerate(stall_counts):
            self.assertEqual(sc, 0, msg=f"Frame {i} had {sc} stall cycles")

    def test_runtime_rate_change(self):
        """CSR rate change mid-traffic — spacing decreases when rate increases."""
        frame_len    = eth_mtu
        frame_charge = eth_mtu
        rate_slow    = RATE_10MBPS           # 10 Mbps
        rate_fast    = RATE_10MBPS * 4       # 40 Mbps
        n_frames     = 6
        frames       = [make_frame(frame_len, seed=i*11) for i in range(n_frames)]
        dut          = DUT(rate=rate_slow, burst=frame_charge)

        refill_slow = expected_refill_cycles(frame_charge, rate_slow)

        def sender():
            for _ in range(SENDER_START_DELAY_ETH_CYCLES):
                yield
            for f in frames:
                yield from send_frame(dut.limiter.sink, f)

        def control():
            yield dut.limiter._rate.storage.eq(rate_slow)
            yield dut.limiter._burst.storage.eq(frame_charge)
            for _ in range(CSR_COMMIT_DELAY_SYS_CYCLES):
                yield
            yield dut.limiter._enable.storage.eq(1)

        def rate_switch():
            # Wait for first 3 frames at 10 Mbps, then update the sys-side CSR.
            yield from wait_for_frames(dut, 3, 4 * refill_slow)
            # In simulation, the CSR storage can be driven directly from this
            # generator; the limiter still samples the synchronized value in IDLE.
            yield dut.limiter._rate.storage.eq(rate_fast)

        def waiter():
            yield from wait_for_frames(dut, n_frames, n_frames * refill_slow + 512)

        run_dut_with_generators(dut, [control()], [sender(), rate_switch(), waiter()])

        self.assertEqual(len(dut.monitor.frames), n_frames)
        for tx, expected in zip(dut.monitor.frames, frames):
            self.assertEqual(tx, expected)
        self.assertEqual(dut.monitor.gaps, [])

        spacings = [dut.monitor.starts[i+1] - dut.monitor.starts[i]
                    for i in range(n_frames - 1)]

        # Early spacings (10 Mbps) should be much longer than late spacings (40 Mbps).
        # Use frame index 1→2 as slow sample, index 4→5 as fast sample.
        slow_spacing = spacings[1]
        fast_spacing = spacings[-1]
        self.assertGreater(slow_spacing, fast_spacing * 2,
            msg=f"Slow spacing ({slow_spacing}) should be >2x fast spacing ({fast_spacing})")
