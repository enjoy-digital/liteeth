#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *
from migen.sim import passive

from litex.gen import *

from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.phy.serial.basex.pcs import PCS
from liteeth.phy.serial.basex.wrappers.serdes_gatemate import GateMate_1000BASEX

# Helpers ------------------------------------------------------------------------------------------

PCS32_TIMINGS = dict(
    check_period    = 256/31.25e6,
    breaklink_time  = 16/31.25e6,
    more_ack_time   = 16/31.25e6,
    eth_tx_clk_freq = 31.25e6,
)

PREAMBLE = [0x55]*7 + [0xd5]

def frames_for_all_lengths():
    # Lengths 60..67 cover the four last-word byte enables, twice.
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
def receive_frames(source, frames, errors, nbytes=4):
    frame = []
    yield source.ready.eq(1)
    while True:
        if (yield source.valid):
            data = (yield source.data)
            be   = (yield source.be)
            frame += [(data >> 8*n) & 0xff for n in range(nbytes) if (be >> n) & 1]
            if (yield source.error):
                errors.append(len(frames))
            if (yield source.last):
                frames.append(frame)
                frame = []
        yield

@passive
def trace_code_groups(data, char_is_k, trace):
    while True:
        d = (yield data)
        k = (yield char_is_k)
        trace.extend([((d >> 8*n) & 0xff, (k >> n) & 1) for n in range(4)])
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

# Lane Shifter -------------------------------------------------------------------------------------

class LaneShifter(LiteXModule):
    """Models the transceiver word alignment: delays the 32-bit code-group stream by `offset` lanes.
    `corrupt` replaces lane 1 with /V/ (K30.7), `breaklink` sends empty /C/ words (link partner
    restarting Auto-Negotiation)."""
    def __init__(self, offset=0):
        self.i_data    = Signal(32)
        self.i_k       = Signal(4)
        self.o_data    = Signal(32)
        self.o_k       = Signal(4)
        self.corrupt   = Signal()
        self.breaklink = Signal()

        # # #

        prev_data = Signal(32)
        prev_k    = Signal(4)
        self.sync += [
            prev_data.eq(self.i_data),
            prev_k.eq(self.i_k),
        ]
        self.comb += [
            self.o_data.eq(Cat(prev_data, self.i_data)[32 - 8*offset:64 - 8*offset]),
            self.o_k.eq(Cat(prev_k, self.i_k)[4 - offset:8 - offset]),
            If(self.breaklink,
                self.o_data.eq(Cat(C(K(28, 5), 8), C(D(21, 5), 8), C(0, 16))),
                self.o_k.eq(0b0001),
            ),
            If(self.corrupt,
                self.o_data[8:16].eq(K(30, 7)),
                self.o_k[1].eq(1),
            ),
        ]

# PCS32 <-> PCS32 ----------------------------------------------------------------------------------

class PCS32LoopbackDUT(LiteXModule):
    """Two PCS32 connected back to back through their 32-bit code-group interfaces."""
    def __init__(self, offset_ab=0, offset_ba=0, **kwargs):
        self.cd_a_tx = ClockDomain()
        self.cd_a_rx = ClockDomain()
        self.cd_b_tx = ClockDomain()
        self.cd_b_rx = ClockDomain()
        pcs_kwargs = dict(PCS32_TIMINGS, with_csr=False, **kwargs)
        self.a  = ClockDomainsRenamer({"eth_tx": "a_tx", "eth_rx": "a_rx"})(PCS(dw=32, **pcs_kwargs))
        self.b  = ClockDomainsRenamer({"eth_tx": "b_tx", "eth_rx": "b_rx"})(PCS(dw=32, **pcs_kwargs))
        self.ab = ClockDomainsRenamer("b_rx")(LaneShifter(offset_ab))
        self.ba = ClockDomainsRenamer("a_rx")(LaneShifter(offset_ba))
        for src, link, dst in [(self.a, self.ab, self.b), (self.b, self.ba, self.a)]:
            self.comb += [
                link.i_data.eq(src.tx.data),
                link.i_k.eq(src.tx.char_is_k),
                dst.rx.data.eq(link.o_data),
                dst.rx.char_is_k.eq(link.o_k),
            ]

PCS32_CLOCKS = {"a_tx": 10, "a_rx": 10, "b_tx": 10, "b_rx": 10}


class TestPCS32(unittest.TestCase):
    def check_loopback(self, offset_ab=0, offset_ba=0, **kwargs):
        dut    = PCS32LoopbackDUT(offset_ab, offset_ba, **kwargs)
        frames = frames_for_all_lengths()
        rx_a, rx_b, err_a, err_b = [], [], [], []
        def gen_a():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.a.sink, frames, gap=3) # Minimum IFG (12 bytes).
            for _ in range(64):
                yield
        def gen_b():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, frames, gap=3)
            for _ in range(64):
                yield
        run_simulation(dut, {
            "a_tx": gen_a(),
            "b_tx": gen_b(),
            "a_rx": receive_frames(dut.a.source, rx_a, err_a),
            "b_rx": receive_frames(dut.b.source, rx_b, err_b),
        }, clocks=PCS32_CLOCKS)
        self.assertEqual(rx_b, frames)
        self.assertEqual(rx_a, frames)
        self.assertEqual(err_a + err_b, [])

    def test_loopback(self):
        self.check_loopback()

    def test_loopback_lane_offsets(self):
        # Without Auto-Negotiation (/C/ is only decoded on lane 0): /I/ and /S/ on any lane.
        for offset_ab, offset_ba in [(1, 3), (2, 2), (3, 1)]:
            with self.subTest(offset_ab=offset_ab, offset_ba=offset_ba):
                self.check_loopback(offset_ab, offset_ba, with_autoneg=False)

    def test_link_partner_restart(self):
        # Link partner restarting Auto-Negotiation after link up: link goes down and comes back.
        dut = PCS32LoopbackDUT()
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield dut.ba.breaklink.eq(1)
            for _ in range(200):
                yield
            self.assertEqual((yield dut.a.link_up), 0)
            yield dut.ba.breaklink.eq(0)
            yield from wait_link_up(self, [dut.a, dut.b])
        run_simulation(dut, {"a_tx": generator()}, clocks=PCS32_CLOCKS)

    def test_rx_error_ends_packet(self):
        # An invalid K-character inside a packet ends it (no repeated words), next ones are OK.
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()[:3]
        rx, err = [], []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, frames, gap=8)
            for _ in range(64):
                yield
        def corrupter():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(10):
                yield
            yield dut.ba.corrupt.eq(1)
            yield
            yield dut.ba.corrupt.eq(0)
        run_simulation(dut, {
            "b_tx": generator(),
            "a_rx": [corrupter(), receive_frames(dut.a.source, rx, err)],
        }, clocks=PCS32_CLOCKS)
        self.assertEqual(len(rx), len(frames))
        self.assertLess(len(rx[0]), len(frames[0]))
        self.assertEqual(rx[1:], frames[1:])

    def test_rx_truncated_frame(self):
        # Frame cut on the wire by /C/ (link loss): RX ends it instead of repeating the last word,
        # then the link comes back and the next frames are received.
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()
        rx, err = [], []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, [PREAMBLE + list(range(200))])
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(64):
                yield
            yield from send_frames(dut.b.sink, frames)
            for _ in range(64):
                yield
        def cutter():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(16):
                yield
            yield dut.ba.breaklink.eq(1)
            for _ in range(64):
                yield
            yield dut.ba.breaklink.eq(0)
        run_simulation(dut, {
            "b_tx": generator(),
            "a_rx": [cutter(), receive_frames(dut.a.source, rx, err)],
        }, clocks=PCS32_CLOCKS)
        self.assertLess(len(rx[0]), 208) # Truncated, not flooded.
        self.assertEqual(rx[1:], frames)

    def test_tx_idle(self):
        # Idle words are 2 /I1/ ordered sets: K28.5 D5.6 K28.5 D5.6.
        dut   = PCS32LoopbackDUT()
        trace = []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(32):
                yield
        tracer = trace_code_groups(dut.a.tx.data, dut.a.tx.char_is_k, trace)
        run_simulation(dut, {"a_tx": [generator(), tracer]}, clocks=PCS32_CLOCKS)
        idle = [(K(28, 5), 1), (D(5, 6), 0)]*2
        self.assertEqual(trace[-16*4:], idle*16)

    def test_tx_end_of_packet(self):
        # /T/ is followed by /R/ (and a 2nd /R/ if needed) so that the next ordered set starts on
        # an even code-group.
        dut    = PCS32LoopbackDUT()
        frames = frames_for_all_lengths()
        trace  = []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.a.sink, frames, gap=0)
            for _ in range(8):
                yield
        tracer = trace_code_groups(dut.a.tx.data, dut.a.tx.char_is_k, trace)
        run_simulation(dut, {"a_tx": [generator(), tracer]}, clocks=PCS32_CLOCKS)
        ends = [i for i, cg in enumerate(trace) if cg == (K(29, 7), 1)]
        self.assertEqual(len(ends), len(frames))
        for i in ends:
            n = i + 1
            while trace[n] == (K(23, 7), 1):
                n += 1
            self.assertGreater(n, i + 1)       # At least one /R/.
            self.assertEqual(n % 2, 0)         # Next ordered set on an even code-group.
            self.assertIn(trace[n], [(K(28, 5), 1), (K(27, 7), 1)])

# PCS (8-bit) <-> PCS32 ----------------------------------------------------------------------------

class PCS8To32DUT(LiteXModule):
    """8-bit PCS (fabric 8b/10b, used as reference) linked to a PCS32 through a model of the hard
    8b/10b coder: 4 code-groups per 32-bit cycle, with commas aligned on lane 0 (as done by the
    CC_SERDES comma alignment; PCS32 only decodes /C/ on lane 0)."""
    def __init__(self):
        self.cd_p8  = ClockDomain()
        self.cd_p32 = ClockDomain()
        self.p8  = ClockDomainsRenamer({"eth_tx": "p8", "eth_rx": "p8"})(
            PCS(lsb_first=True, sgmii=False, with_csr=False, eth_tx_clk_freq=125e6,
                check_period=1024/125e6, breaklink_time=64/125e6, more_ack_time=64/125e6))
        self.p32 = ClockDomainsRenamer({"eth_tx": "p32", "eth_rx": "p32"})(
            PCS(dw=32, with_csr=False, **PCS32_TIMINGS))

        # # #

        # 8-bit TX -> 32-bit RX: decode and gather 4 code-groups.
        self.decoder = decoder = ClockDomainsRenamer("p8")(Decoder(lsb_first=True))
        gather_data = Signal(32)
        gather_k    = Signal(4)
        count       = Signal(2, reset=1)
        word_data   = Signal(32)
        word_k      = Signal(4)
        self.comb += [
            decoder.input.eq(self.p8.tbi_tx),
            decoder.ce.eq(1),
        ]
        self.sync.p8 += [
            count.eq(count + 1),
            gather_data.eq(Cat(gather_data[8:], decoder.d)),
            gather_k.eq(Cat(gather_k[1:], decoder.k)),
            If(count == 0,
                word_data.eq(gather_data),
                word_k.eq(gather_k),
            )
        ]
        self.comb += [
            self.p32.rx.data.eq(word_data),
            self.p32.rx.char_is_k.eq(word_k),
        ]

        # 32-bit TX -> 8-bit RX: serialize 4 code-groups and encode.
        self.encoder = encoder = ClockDomainsRenamer("p8")(Encoder(lsb_first=True))
        lane     = Signal(2)
        ser_data = Signal(32)
        ser_k    = Signal(4)
        self.sync.p8 += [
            lane.eq(lane + 1),
            If(lane == 3,
                ser_data.eq(self.p32.tx.data),
                ser_k.eq(self.p32.tx.char_is_k),
            ).Else(
                ser_data.eq(ser_data[8:]),
                ser_k.eq(ser_k[1:]),
            )
        ]
        self.comb += [
            encoder.d[0].eq(ser_data[:8]),
            encoder.k[0].eq(ser_k[0]),
            self.p8.tbi_rx.eq(encoder.output[0]),
            self.p8.tbi_rx_ce.eq(1),
        ]


class TestPCS8To32Interop(unittest.TestCase):
    def test_interop(self):
        dut    = PCS8To32DUT()
        frames = frames_for_all_lengths()
        rx8, rx32, err8, err32 = [], [], [], []
        def p32_tx():
            yield from wait_link_up(self, [dut.p8, dut.p32])
            yield from send_frames(dut.p32.sink, frames)
            for _ in range(256):
                yield
        @passive
        def p8_tx():
            yield from wait_link_up(self, [dut.p8, dut.p32], timeout=100000)
            for frame in frames:
                for i, byte in enumerate(frame):
                    yield dut.p8.sink.valid.eq(1)
                    yield dut.p8.sink.data.eq(byte)
                    yield dut.p8.sink.be.eq(1)
                    yield dut.p8.sink.last.eq(i == len(frame) - 1)
                    yield
                    while not (yield dut.p8.sink.ready):
                        yield
                yield dut.p8.sink.valid.eq(0)
                for _ in range(12):
                    yield
        run_simulation(dut, {
            "p32": [p32_tx(), receive_frames(dut.p32.source, rx32, err32)],
            "p8" : [p8_tx(),  receive_frames(dut.p8.source,  rx8,  err8, nbytes=1)],
        }, clocks={"p8": 4, "p32": 16})
        self.assertEqual(rx8,  frames)
        self.assertEqual(rx32, frames)
        self.assertEqual(err8 + err32, [])

# PHY ----------------------------------------------------------------------------------------------

class CCSerDesLoopback:
    """Lowers CC_SERDES to a 32-bit loopback (and CC_BUFG to nothing: clocks come from the sim)."""
    def lower(self, instance):
        model = Module()
        if instance.of == "CC_SERDES":
            model.comb += [
                instance.get_io("RX_DATA_O").eq(instance.get_io("TX_DATA_I")),
                instance.get_io("RX_CHAR_IS_K_O").eq(instance.get_io("TX_CHAR_IS_K_I")),
                instance.get_io("RX_RESET_DONE_O").eq(1),
                instance.get_io("TX_RESET_DONE_O").eq(1),
            ]
        else:
            assert instance.of == "CC_BUFG"
        return model


class TestGateMate1000BASEX(unittest.TestCase):
    def test_loopback(self):
        dut = GateMate_1000BASEX(sys_clk_freq=1e3, with_csr=False,
            pcs_kwargs=dict(PCS32_TIMINGS, with_csr=False))
        dut.cd_sys = ClockDomain("sys")
        frames = frames_for_all_lengths()[:4]
        rx, err = [], []
        def generator():
            yield from wait_link_up(self, [dut])
            yield from send_frames(dut.sink, frames)
            for _ in range(64):
                yield
        run_simulation(dut, {
            "eth_tx": generator(),
            "eth_rx": receive_frames(dut.source, rx, err),
        }, clocks={"sys": 20, "eth_tx": 10, "eth_rx": 10},
           special_overrides={Instance: CCSerDesLoopback()})
        self.assertEqual(rx, frames)
        self.assertEqual(err, [])


if __name__ == "__main__":
    unittest.main()
