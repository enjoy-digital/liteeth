#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from liteeth.common import *
from liteeth.mac.core import LiteEthMACCore
from liteeth.phy.serial.baset import *

# Helpers ------------------------------------------------------------------------------------------

PREAMBLE = [0x55]*7 + [0xd5]

def frame_bits(data):
    return [(byte >> i) & 1 for byte in data for i in range(8)]

def send_frames(sink, frames):
    for frame in frames:
        for i, byte in enumerate(frame):
            yield sink.valid.eq(1)
            yield sink.data.eq(byte)
            yield sink.be.eq(1)
            yield sink.last.eq(i == len(frame) - 1)
            yield
            while not (yield sink.ready):
                yield
    yield sink.valid.eq(0)

def collect_frames(source, frames, nframes, max_cycles):
    frame = []
    for _ in range(max_cycles):
        if (yield source.valid):
            frame.append((yield source.data))
            if (yield source.last):
                frames.append(frame)
                frame = []
                if len(frames) == nframes:
                    return
        yield

def line_waveform(events):
    """Return the line as a list of (start time in ns, level) segments.

    events: ("idle", ns), ("nlp",), ("frame", data, tp_idl_ns), ("noise", n) (short glitches).
    """
    segments = []
    t = 0.0
    def level(value, duration):
        nonlocal t
        segments.append((t, value))
        t += duration
    for event in events:
        if event[0] == "idle":
            level(0, event[1])
        elif event[0] == "nlp":
            level(1, 100)
        elif event[0] == "frame":
            for bit in frame_bits(event[1]):
                level(1 - bit, 50)
                level(bit,     50)
            level(1, event[2])
    segments.append((t, 0))
    return segments

def sample_waveform(segments, period, phase=0.0, ppm=0, jitter=0.0, inverted=False, seed=0):
    """Sample the line with a receiver clock of the given period (ns), frequency offset and jitter
    on each transition (ns)."""
    rng = random.Random(seed)
    edges = [(start + rng.uniform(-jitter, jitter), level) for start, level in segments]
    period *= 1 + ppm*1e-6
    samples = []
    t = phase
    i = 0
    end = segments[-1][0] + 1000
    while t < end:
        while (i + 1) < len(edges) and edges[i + 1][0] <= t:
            i += 1
        samples.append(edges[i][1] ^ inverted)
        t += period
    return samples

# TX -----------------------------------------------------------------------------------------------

class TestBASETTX(unittest.TestCase):
    clk_freq = 100e6

    def run_tx(self, frames, cycles, nlp_period=baset_nlp_period):
        dut   = LiteEthPHY10BASETTX(self.clk_freq, nlp_period=nlp_period)
        trace = []
        def monitor():
            for _ in range(cycles):
                trace.append(((yield dut.o), (yield dut.oe)))
                yield
        run_simulation(dut, [send_frames(dut.sink, frames), monitor()])
        return trace

    def bursts(self, trace):
        """Return (start, end) of each driven burst."""
        bursts = []
        start  = None
        for n, (o, oe) in enumerate(trace):
            if oe and start is None:
                start = n
            if not oe and start is not None:
                bursts.append((start, n))
                start = None
        return bursts

    def decode(self, trace, start, nbits):
        half = int(self.clk_freq*baset_bit_time/2)
        bits = []
        for n in range(nbits):
            first  = trace[start + (2*n + 0)*half:start + (2*n + 1)*half]
            second = trace[start + (2*n + 1)*half:start + (2*n + 2)*half]
            # Each half bit has a constant level, and a transition in the middle of the bit.
            self.assertEqual(len(set(first)),  1)
            self.assertEqual(len(set(second)), 1)
            self.assertNotEqual(first[0][0], second[0][0])
            bits.append(second[0][0])
        return bits

    def test_frames(self):
        frames = [PREAMBLE + list(range(64)), PREAMBLE + [0xff, 0x00, 0xa5]*20]
        trace  = self.run_tx(frames, 20000)
        bursts = self.bursts(trace)
        self.assertEqual(len(bursts), 2)
        bit_cycles = int(self.clk_freq*baset_bit_time)
        prev_end = None
        for frame, (start, end) in zip(frames, bursts):
            nbits = 8*len(frame)
            self.assertEqual(self.decode(trace, start, nbits), frame_bits(frame))
            # TP_IDL: high for at least 250ns after the last bit, then released.
            tp_idl = trace[start + nbits*bit_cycles:end]
            self.assertTrue(all(o for o, oe in tp_idl))
            self.assertGreaterEqual(len(tp_idl), 250e-9*self.clk_freq)
            self.assertLessEqual(len(tp_idl), 400e-9*self.clk_freq)
            # Inter-frame gap: at least 96 bit times.
            if prev_end is not None:
                self.assertGreaterEqual(start - prev_end, 96*bit_cycles)
            prev_end = start + nbits*bit_cycles

    def test_link_pulses(self):
        nlp_period = 20e-6
        trace  = self.run_tx([], 10000, nlp_period=nlp_period)
        bursts = self.bursts(trace)
        self.assertGreaterEqual(len(bursts), 4)
        for start, end in bursts:
            self.assertEqual(end - start, round(baset_nlp_time*self.clk_freq))
            self.assertTrue(all(o for o, oe in trace[start:end]))
        for (s0, _), (s1, _) in zip(bursts, bursts[1:]):
            self.assertEqual(s1 - s0, round((nlp_period + baset_nlp_time)*self.clk_freq))

    def test_underrun(self):
        # A frame starving mid-way is aborted and its remainder dropped; the next one is sent whole.
        dut   = LiteEthPHY10BASETTX(self.clk_freq)
        trace = []
        def monitor():
            for _ in range(10000):
                trace.append(((yield dut.o), (yield dut.oe)))
                yield
        def incomplete():
            for byte in PREAMBLE:
                yield dut.sink.valid.eq(1)
                yield dut.sink.data.eq(byte)
                yield dut.sink.last.eq(0)
                yield
                while not (yield dut.sink.ready):
                    yield
            yield dut.sink.valid.eq(0)
            for _ in range(2000):
                yield
            yield from send_frames(dut.sink, [[0x34, 0x56], PREAMBLE + [0x78]])
        run_simulation(dut, [incomplete(), monitor()])
        bursts = self.bursts(trace)
        self.assertEqual(len(bursts), 2)
        start, end = bursts[1]
        self.assertEqual(self.decode(trace, start, 72), frame_bits(PREAMBLE + [0x78]))

# RX -----------------------------------------------------------------------------------------------

class TestBASETRX(unittest.TestCase):
    def run_rx(self, samples, clk_freq, nframes):
        dut    = LiteEthPHY10BASETRX(clk_freq)
        frames = []
        stats  = {"link_pulses": 0, "inverted": None}
        def stimulus():
            for sample in samples:
                yield dut.i.eq(sample)
                yield
        def monitor():
            yield from collect_frames(dut.source, frames, nframes, len(samples))
        @passive
        def pulses():
            while True:
                stats["link_pulses"] += (yield dut.link_pulse)
                stats["inverted"]     = (yield dut.inverted)
                yield
        run_simulation(dut, [stimulus(), monitor(), pulses()])
        return frames, stats

    def check(self, clk_freq, ppm=0, jitter=0.0, inverted=False, phases=(0.0, 0.37, 0.71)):
        payloads = [list(range(64)), [0xff]*30 + [0x00]*30, [0xa5, 0x5a, 0x01, 0x80]*16]
        events   = [("idle", 2000), ("nlp",), ("idle", 3000)]
        for payload in payloads:
            events += [("frame", PREAMBLE + payload, 300), ("idle", 9600)]
        events += [("nlp",), ("idle", 2000)]
        segments = line_waveform(events)
        for phase in phases:
            with self.subTest(clk_freq=clk_freq, ppm=ppm, jitter=jitter, inverted=inverted, phase=phase):
                samples = sample_waveform(segments, 1e9/clk_freq, phase=phase*1e9/clk_freq,
                    ppm=ppm, jitter=jitter, inverted=inverted, seed=int(phase*100))
                frames, stats = self.run_rx(samples, clk_freq, len(payloads))
                self.assertEqual(frames, [[0xd5] + payload for payload in payloads])
                self.assertEqual(stats["inverted"], int(inverted))
                if not inverted:
                    self.assertEqual(stats["link_pulses"], 2)

    def test_clocks(self):
        for clk_freq in [60e6, 100e6, 125e6]:
            self.check(clk_freq)

    def test_frequency_offset(self):
        # IEEE 802.3 allows +/-100ppm on each side.
        for ppm in [-500, 500]:
            self.check(100e6, ppm=ppm)

    def test_jitter(self):
        self.check(100e6, jitter=10.0)
        self.check(60e6,  jitter=5.0)

    def test_inverted(self):
        self.check(100e6, inverted=True)

# MAC Loopback -------------------------------------------------------------------------------------

class LoopbackPHY(LiteXModule):
    dw = 8
    def __init__(self, tx_clk_freq, rx_clk_freq):
        self.tx_clk_freq = tx_clk_freq
        self.rx_clk_freq = rx_clk_freq
        self.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHY10BASETTX(tx_clk_freq))
        self.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHY10BASETRX(rx_clk_freq))
        # Line: TX output, released to idle (low) when not driven, sampled by RX.
        self.specials += MultiReg(self.tx.o & self.tx.oe, self.rx.i, odomain="eth_rx")
        self.sink, self.source = self.tx.sink, self.rx.source


class TestBASETMAC(unittest.TestCase):
    def test_mac_loopback(self):
        # TX at 100MHz, RX at 83.33MHz: a frame goes through the MAC preamble/CRC inserters, the
        # line and the MAC preamble/CRC checkers.
        phy  = LoopbackPHY(100e6, 1e9/12)
        core = LiteEthMACCore(phy=phy, dw=8, with_preamble_crc=True, sys_clk_freq=100e6)
        dut  = LiteXModule()
        dut.phy, dut.core = phy, core
        frames   = [[(i*7) & 0xff for i in range(60)]]
        received = []
        def source():
            yield from send_frames(core.sink, frames)
        def sink():
            yield core.source.ready.eq(1)
            yield from collect_frames(core.source, received, len(frames), 100000)
        run_simulation(dut, {"sys": [source(), sink()]},
            clocks={"sys": 10, "eth_tx": 10, "eth_rx": 12})
        self.assertEqual(received, frames)


if __name__ == "__main__":
    unittest.main()
