#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Joel Stanley <jms@oss.tenstorrent.com>
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *
from litex.gen.sim import run_simulation

from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.phy.pcs_1000basex    import PCS
from liteeth.phy.us_lvds_1000basex import (
    USLVDSCommaAligner,
    USLVDSPhaseDetector,
    USLVDSTXGearbox,
    USLVDSRXGearbox,
)

# Helpers ------------------------------------------------------------------------------------------

class BitStream:
    """Re-chunks words of one width into words of another width, bit 0 first, with an offset."""
    def __init__(self, offset=0):
        self.bits = [0]*offset

    def push(self, word, width):
        self.bits += [(word >> i) & 1 for i in range(width)]

    def available(self, width):
        return len(self.bits) >= width

    def pop(self, width):
        bits, self.bits = self.bits[:width], self.bits[width:]
        return sum(b << i for i, b in enumerate(bits))


IDLE_SEQ = [(1, K(28, 5)), (0, D(16, 2))]

# Comma Aligner ------------------------------------------------------------------------------------

class CommaAlignerDUT(LiteXModule):
    def __init__(self):
        self.encoder = Encoder(lsb_first=True)
        self.aligner = USLVDSCommaAligner()
        self.decoder = Decoder(lsb_first=True)
        self.comb += [
            self.decoder.input.eq(self.aligner.source.data),
            self.decoder.ce.eq(self.aligner.source.valid),
        ]


class TestCommaAligner(unittest.TestCase):
    def run_aligner(self, offset, data_bytes):
        dut     = CommaAlignerDUT()
        stream  = BitStream(offset=offset)
        decoded = []

        def generator():
            # Idles, then data, then idles again.
            seq = IDLE_SEQ*16 + [(0, d) for d in data_bytes] + IDLE_SEQ*16
            yield dut.aligner.align.eq(1)
            valid = 0
            for i, (k, d) in enumerate(seq + [(1, K(28, 5))]*4):
                # The decoder is registered: sample it one cycle after a valid code-group.
                if valid:
                    decoded.append(((yield dut.decoder.k),
                                    (yield dut.decoder.d),
                                    (yield dut.decoder.invalid)))
                valid = (yield dut.aligner.source.valid)
                yield dut.encoder.k[0].eq(k)
                yield dut.encoder.d[0].eq(d)
                # Encoder output is registered: take the previous code-group.
                if i > 0:
                    stream.push((yield dut.encoder.output[0]), 10)
                if stream.available(10):
                    yield dut.aligner.sink.data.eq(stream.pop(10))
                    yield dut.aligner.sink.valid.eq(1)
                else:
                    yield dut.aligner.sink.valid.eq(0)
                if i == 32:
                    # Stop searching once the idles have locked the offset.
                    yield dut.aligner.align.eq(0)
                yield

        run_simulation(dut, generator())
        return decoded

    def check(self, offset):
        # Random payload, avoiding the /I2/ data byte so idles can be filtered out below.
        data_bytes = [d for d in [random.randrange(256) for _ in range(40)] if d != D(16, 2)][:32]
        decoded    = self.run_aligner(offset, data_bytes)
        # Skip the pipeline warmup and the alignment phase.
        decoded = decoded[8:]
        self.assertFalse(any(invalid for _, _, invalid in decoded),
            "invalid code-groups after alignment")
        # The data bytes must appear in order between the idles.
        payload = [d for k, d, _ in decoded if not k and d not in (D(16, 2),)]
        self.assertEqual(payload, data_bytes)

    def test_offsets(self):
        random.seed(1)
        for offset in range(10):
            with self.subTest(offset=offset):
                self.check(offset)

    def test_restart(self):
        dut    = CommaAlignerDUT()
        stream = BitStream(offset=3)
        trace  = []

        def generator():
            yield dut.aligner.align.eq(1)
            for i in range(64):
                k, d = IDLE_SEQ[i % 2]
                yield dut.encoder.k[0].eq(k)
                yield dut.encoder.d[0].eq(d)
                if i > 0:
                    stream.push((yield dut.encoder.output[0]), 10)
                if stream.available(10):
                    yield dut.aligner.sink.data.eq(stream.pop(10))
                    yield dut.aligner.sink.valid.eq(1)
                else:
                    yield dut.aligner.sink.valid.eq(0)
                yield dut.aligner.restart.eq(i == 32)
                trace.append(((yield dut.aligner.shift), (yield dut.decoder.invalid)))
                yield

        run_simulation(dut, generator())
        # Aligned on the idles, dropped by the restart, aligned again on the next comma.
        self.assertEqual(trace[32], (3, 0))
        self.assertEqual(trace[34][0], 0)
        self.assertEqual(trace[36][0], 3)
        self.assertTrue(all(invalid == 0 for _, invalid in trace[40:]))

# Phase Detector -----------------------------------------------------------------------------------

class PhaseDetectorDUT(LiteXModule):
    def __init__(self, threshold=16):
        self.data = Signal(8)
        self.mon  = Signal(8)
        self.pd   = USLVDSPhaseDetector(self.data, self.mon, threshold=threshold)


class TestPhaseDetector(unittest.TestCase):
    def run_pd(self, monitor_late, cycles=256, invert=0, threshold=16):
        """The monitor sees the line half a bit later, so monitor bit i is sampled at the
           transition between data bits i-1 and i.
           monitor_late=False: monitor still shows the previous bit (sampling clock early).
           monitor_late=True : monitor already shows the current bit (sampling clock late)."""
        dut   = PhaseDetectorDUT(threshold=threshold)
        steps = []

        def generator():
            random.seed(2)
            bits = [random.randrange(2) for _ in range(8*cycles + 1)]
            yield dut.pd.invert.eq(invert)
            psdone_countdown = 0
            for c in range(cycles):
                word = bits[8*c+1:8*c+9]
                prev = bits[8*c:8*c+8]
                mon  = word if monitor_late else prev
                yield dut.data.eq(sum(b << i for i, b in enumerate(word)))
                yield dut.mon.eq(sum(b << i for i, b in enumerate(mon)))
                # MMCM model: PSDONE 12 cycles after PSEN.
                yield dut.pd.psdone.eq(psdone_countdown == 1)
                if psdone_countdown > 0:
                    psdone_countdown -= 1
                if (yield dut.pd.psen):
                    self.assertEqual(psdone_countdown, 0, "PSEN while a phase shift is in progress")
                    steps.append((yield dut.pd.psincdec))
                    psdone_countdown = 12
                elif psdone_countdown > 0:
                    # Votes taken while the phase shifts are discarded.
                    self.assertEqual((yield dut.pd.acc), 0, "accumulating during the phase shift")
                yield
            self.assertEqual((yield dut.pd.inc_count), steps.count(1))
            self.assertEqual((yield dut.pd.dec_count), steps.count(0))

        run_simulation(dut, generator())
        return steps

    def test_early_increments(self):
        steps = self.run_pd(monitor_late=False)
        self.assertGreater(len(steps), 4)
        self.assertTrue(all(s == 1 for s in steps))

    def test_late_decrements(self):
        steps = self.run_pd(monitor_late=True)
        self.assertGreater(len(steps), 4)
        self.assertTrue(all(s == 0 for s in steps))

    def test_invert(self):
        steps = self.run_pd(monitor_late=False, invert=1)
        self.assertGreater(len(steps), 4)
        self.assertTrue(all(s == 0 for s in steps))

    def test_saturation(self):
        # The accumulator saturates at 127 - 8: reachable, one more is not.
        steps = self.run_pd(monitor_late=False, threshold=127 - 8)
        self.assertGreater(len(steps), 0)
        self.assertTrue(all(s == 1 for s in steps))
        steps = self.run_pd(monitor_late=False, threshold=127 - 8 + 1)
        self.assertEqual(steps, [])

# Gearbox Loopback ---------------------------------------------------------------------------------

class GearboxLoopbackDUT(LiteXModule):
    """Two PCS linked through the TX/RX gearboxes, the 8-bit words are wired by the testbench."""
    def __init__(self):
        pcs_params = dict(
            lsb_first      = True,
            check_period   = 32/125e6,
            breaklink_time = 1/125e6,
            more_ack_time  = 1/125e6,
            sgmii_ack_time = 1/125e6,
        )
        self.pcs_a = PCS(**pcs_params)
        self.pcs_b = PCS(**pcs_params)
        self.tx_a  = USLVDSTXGearbox()
        self.tx_b  = USLVDSTXGearbox()
        self.rx_a  = USLVDSRXGearbox()
        self.rx_b  = USLVDSRXGearbox()
        for pcs, tx, rx in [(self.pcs_a, self.tx_a, self.rx_a), (self.pcs_b, self.tx_b, self.rx_b)]:
            self.comb += [
                tx.sink.valid.eq(1),
                tx.sink.data.eq(pcs.tbi_tx),
                tx.source.ready.eq(1),
                rx.sink.valid.eq(1),
                rx.source.ready.eq(1),
                pcs.tbi_rx.eq(rx.source.data),
                pcs.tbi_rx_ce.eq(rx.source.valid),
            ]
            self.specials += MultiReg(pcs.align, rx.align, "eth_rx")


class TestGearboxLoopback(unittest.TestCase):
    def test_link_up(self):
        dut  = GearboxLoopbackDUT()
        done = {"link": False}

        def wire(tx, rx, offset):
            # Runs in eth_tx_div/eth_rx_div: forward the 8-bit words with a bit offset.
            stream = BitStream(offset=offset)
            while not done["link"]:
                if (yield tx.source.valid):
                    stream.push((yield tx.source.data), 8)
                if stream.available(8):
                    yield rx.sink.data.eq(stream.pop(8))
                yield

        def checker():
            for i in range(4096):
                if (yield dut.pcs_a.link_up) and (yield dut.pcs_b.link_up):
                    break
                yield
            done["link"] = True
            self.assertTrue((yield dut.pcs_a.link_up) and (yield dut.pcs_b.link_up),
                "link not established")

        run_simulation(dut,
            generators = {
                "eth_tx_div": [wire(dut.tx_a, dut.rx_b, offset=3),
                               wire(dut.tx_b, dut.rx_a, offset=7)],
                "eth_tx":     [checker()],
            },
            clocks = {
                "sys"        : 10,
                "eth_tx"     : 10,
                "eth_rx"     : 10,
                "eth_tx_div" : 8,
                "eth_rx_div" : 8,
            },
        )


if __name__ == "__main__":
    unittest.main()
