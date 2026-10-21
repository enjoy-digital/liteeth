#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *
from migen.sim import passive

from litex.gen import *

from litex.soc.cores.code_8b10b import K, D

from liteeth.phy.serial.basex.pcs32 import PCS32

# Helpers ------------------------------------------------------------------------------------------

PCS_TIMINGS = dict(
    check_period    = 256/31.25e6,
    breaklink_time  = 16/31.25e6,
    more_ack_time   = 16/31.25e6,
    eth_tx_clk_freq = 31.25e6,
    with_csr        = False,
)

PREAMBLE = [0x55]*7 + [0xd5]

def frames_for_all_lengths():
    # Lengths 60..67 cover the four last-beat byte enables, twice.
    return [PREAMBLE + [(i*7 + n) & 0xff for i in range(n)] for n in range(60, 68)]

def send_frames(sink, frames, gap=3):
    for frame in frames:
        words = [frame[i:i + 4] for i in range(0, len(frame), 4)]
        for i, word in enumerate(words):
            yield sink.valid.eq(1)
            yield sink.data.eq(sum(b << 8*n for n, b in enumerate(word)))
            yield sink.be.eq((1 << len(word)) - 1)
            yield sink.last.eq(i == len(words) - 1)
            yield
            while not (yield sink.ready):
                yield
        yield sink.valid.eq(0)
        for _ in range(gap):
            yield

@passive
def receive_frames(source, frames, errors):
    frame = []
    yield source.ready.eq(1)
    while True:
        if (yield source.valid):
            data = (yield source.data)
            be   = (yield source.be)
            frame += [(data >> 8*n) & 0xff for n in range(4) if (be >> n) & 1]
            if (yield source.error):
                errors.append(len(frames))
            if (yield source.last):
                frames.append(frame)
                frame = []
        yield

def wait_link_up(test, pcs_list, timeout=20000):
    for _ in range(timeout):
        ups = []
        for pcs in pcs_list:
            ups.append((yield pcs.link_up))
        if all(ups):
            return
        yield
    test.fail("PCS did not establish a link")

# PCS32 Loopback -----------------------------------------------------------------------------------

class PCS32LoopbackDUT(LiteXModule):
    """Two PCS32 connected back to back through their 32-bit code-group interfaces."""
    def __init__(self):
        self.cd_a_tx = ClockDomain()
        self.cd_a_rx = ClockDomain()
        self.cd_b_tx = ClockDomain()
        self.cd_b_rx = ClockDomain()
        self.a = ClockDomainsRenamer({"eth_tx": "a_tx", "eth_rx": "a_rx"})(PCS32(**PCS_TIMINGS))
        self.b = ClockDomainsRenamer({"eth_tx": "b_tx", "eth_rx": "b_rx"})(PCS32(**PCS_TIMINGS))
        self.b_breaklink = Signal() # B -> A: send empty /C/ (link partner restarting autoneg).
        self.b_error     = Signal() # B -> A: send /V/ (K30.7) on lane 1.
        self.comb += [
            self.b.rx.data.eq(self.a.tx.data),
            self.b.rx.char_is_k.eq(self.a.tx.char_is_k),
            self.a.rx.data.eq(self.b.tx.data),
            self.a.rx.char_is_k.eq(self.b.tx.char_is_k),
            If(self.b_breaklink,
                self.a.rx.data.eq(Cat(C(K(28, 5), 8), C(D(21, 5), 8), C(0, 16))),
                self.a.rx.char_is_k.eq(0b0001),
            ),
            If(self.b_error,
                self.a.rx.data[8:16].eq(K(30, 7)),
                self.a.rx.char_is_k[1].eq(1),
            ),
        ]

CLOCKS = {"a_tx": 10, "a_rx": 10, "b_tx": 10, "b_rx": 10}

class TestGateMate1000BASEX(unittest.TestCase):
    def test_loopback(self):
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()
        rx_a, rx_b, err_a, err_b = [], [], [], []
        def gen_a():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.a.sink, frames)
            for _ in range(64):
                yield
        def gen_b():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, frames)
            for _ in range(64):
                yield
        run_simulation(dut, {
            "a_tx": gen_a(),
            "b_tx": gen_b(),
            "a_rx": receive_frames(dut.a.source, rx_a, err_a),
            "b_rx": receive_frames(dut.b.source, rx_b, err_b),
        }, clocks=CLOCKS)
        self.assertEqual(rx_b, frames)
        self.assertEqual(rx_a, frames)
        self.assertEqual(err_a + err_b, [])

    def test_link_partner_restart(self):
        # Link partner restarting autoneg after link up (empty /C/): link must drop and come back.
        dut = PCS32LoopbackDUT()
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield dut.b_breaklink.eq(1)
            for _ in range(200):
                yield
            self.assertEqual((yield dut.a.link_up), 0)
            yield dut.b_breaklink.eq(0)
            yield from wait_link_up(self, [dut.a, dut.b])
        run_simulation(dut, {"a_tx": generator()}, clocks=CLOCKS)

    def test_rx_truncated_frame(self):
        # Frame cut by something else than /T/ (here /C/ after link loss): RX must end the frame
        # instead of repeating the last word, then receive the next frames normally.
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()
        rx, err = [], []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            # Start a long frame, then cut it on the wire.
            yield dut.b.sink.valid.eq(1)
            yield dut.b.sink.data.eq(0x55555555)
            yield dut.b.sink.be.eq(0b1111)
            yield dut.b.sink.last.eq(0)
            for _ in range(8):
                yield
            yield dut.b.sink.valid.eq(0)
            yield dut.b_breaklink.eq(1)
            for _ in range(64):
                yield
            yield dut.b_breaklink.eq(0)
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, frames)
            for _ in range(64):
                yield
        run_simulation(dut, {
            "b_tx": generator(),
            "a_rx": receive_frames(dut.a.source, rx, err),
        }, clocks=CLOCKS)
        self.assertLess(len(rx[0]), 40) # Truncated, not flooded.
        self.assertEqual(rx[1:], frames)

    def test_tx_idle(self):
        # Idle words are 2 /I2/ ordered sets: K28.5 D16.2 K28.5 D16.2.
        dut   = PCS32LoopbackDUT()
        words = []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(16):
                yield
            for _ in range(16):
                words.append(((yield dut.a.tx.data), (yield dut.a.tx.char_is_k)))
                yield
        run_simulation(dut, {"a_tx": generator()}, clocks=CLOCKS)
        idle = K(28, 5) | (D(16, 2) << 8) | (K(28, 5) << 16) | (D(16, 2) << 24)
        self.assertEqual(words, [(idle, 0b0101)]*16)

    def test_tx_end_of_packet(self):
        # /T/ is always followed by /R/ (whatever the number of bytes in the last word).
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()
        trace  = []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.a.sink, frames)
            for _ in range(8):
                yield
        @passive
        def monitor():
            while True:
                data = (yield dut.a.tx.data)
                k    = (yield dut.a.tx.char_is_k)
                trace.extend([((data >> 8*n) & 0xff, (k >> n) & 1) for n in range(4)])
                yield
        run_simulation(dut, {"a_tx": [generator(), monitor()]}, clocks=CLOCKS)
        ends = [i for i, cg in enumerate(trace) if cg == (K(29, 7), 1)]
        self.assertEqual(len(ends), len(frames))
        for i in ends:
            self.assertEqual(trace[i + 1], (K(23, 7), 1))
