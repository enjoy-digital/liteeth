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
from liteeth.phy import GateMate_1000BASEX

# Helpers ------------------------------------------------------------------------------------------

PCS_TIMINGS = dict(
    check_period   = 256/31.25e6,
    breaklink_time = 16/31.25e6,
    more_ack_time  = 16/31.25e6,
)

PREAMBLE = [0x55]*7 + [0xd5]

def frames_for_all_lengths():
    # Lengths 60..67 cover the four last-beat byte enables, twice.
    return [PREAMBLE + [(i*7 + n) & 0xff for i in range(n)] for n in range(60, 68)]

def frame_words(frame):
    words = []
    for i in range(0, len(frame), 4):
        chunk = frame[i:i + 4]
        words.append((sum(b << 8*n for n, b in enumerate(chunk)), (1 << len(chunk)) - 1))
    return words

def send_frames(sink, frames, gap=0):
    for frame in frames:
        words = frame_words(frame)
        for i, (data, be) in enumerate(words):
            yield sink.valid.eq(1)
            yield sink.data.eq(data)
            yield sink.be.eq(be)
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
            for n in range(nbytes):
                if (be >> n) & 1:
                    frame.append((data >> 8*n) & 0xff)
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

# Lane Shifter -------------------------------------------------------------------------------------

class LaneShifter(LiteXModule):
    """Models the transceiver comma alignment putting code-groups on another lane: delays the
    32-bit code-group stream by `offset` lanes."""
    def __init__(self, offset):
        self.i_data = Signal(32)
        self.i_k    = Signal(4)
        self.o_data = Signal(32)
        self.o_k    = Signal(4)
        self.corrupt = Signal() # Replace lane 1 with /V/ (K30.7) when set.

        # # #

        prev_data = Signal(32)
        prev_k    = Signal(4)
        self.sync += [
            prev_data.eq(self.i_data),
            prev_k.eq(self.i_k),
        ]
        data = Signal(32)
        k    = Signal(4)
        self.comb += [
            data.eq(Cat(prev_data, self.i_data)[32 - 8*offset:64 - 8*offset]),
            k.eq(Cat(prev_k, self.i_k)[4 - offset:8 - offset]),
        ]
        self.sync += [
            self.o_data.eq(data),
            self.o_k.eq(k),
            If(self.corrupt,
                self.o_data[8:16].eq(K(30, 7)),
                self.o_k[1].eq(1),
            )
        ]

# 32-bit <-> 32-bit --------------------------------------------------------------------------------

class PCS32LoopbackDUT(LiteXModule):
    def __init__(self, offset_ab=0, offset_ba=0):
        self.cd_a_tx = ClockDomain()
        self.cd_a_rx = ClockDomain()
        self.cd_b_tx = ClockDomain()
        self.cd_b_rx = ClockDomain()
        self.a = ClockDomainsRenamer({"eth_tx": "a_tx", "eth_rx": "a_rx"})(
            PCS(dw=32, eth_tx_clk_freq=31.25e6, **PCS_TIMINGS))
        self.b = ClockDomainsRenamer({"eth_tx": "b_tx", "eth_rx": "b_rx"})(
            PCS(dw=32, eth_tx_clk_freq=31.25e6, **PCS_TIMINGS))
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


class TestPCS32b(unittest.TestCase):
    def check_loopback(self, offset_ab, offset_ba):
        dut    = PCS32LoopbackDUT(offset_ab, offset_ba)
        frames = frames_for_all_lengths()
        rx_a, rx_b, err_a, err_b = [], [], [], []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            # Back-to-back frames A -> B.
            yield from send_frames(dut.a.sink, frames)
            for _ in range(64):
                yield
        def generator_b():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.b.sink, frames, gap=3)
            for _ in range(64):
                yield
        run_simulation(dut, {
                "a_tx": generator(),
                "b_tx": generator_b(),
                "b_rx": receive_frames(dut.b.source, rx_b, err_b),
                "a_rx": receive_frames(dut.a.source, rx_a, err_a),
            }, clocks=PCS32_CLOCKS)
        self.assertEqual(rx_b, frames)
        self.assertEqual(rx_a, frames)
        self.assertEqual(err_a + err_b, [])

    def test_loopback(self):
        for offset_ab, offset_ba in [(0, 0), (2, 0), (0, 2), (1, 3), (3, 1)]:
            with self.subTest(offset_ab=offset_ab, offset_ba=offset_ba):
                self.check_loopback(offset_ab, offset_ba)

    def test_tx_idle_and_end_of_packet(self):
        # Idle is /I1/ /I2/; /T/ is followed by /R/ (and a second /R/ on an odd position) so the
        # next ordered set starts on an even code-group.
        dut   = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(
            PCS(dw=32, with_autoneg=False, eth_tx_clk_freq=31.25e6, **PCS_TIMINGS))
        trace = []
        def generator():
            for n in range(60, 64):
                yield from send_frames(dut.sink, [PREAMBLE + list(range(n))], gap=4)
        @passive
        def monitor():
            while True:
                data = (yield dut.tx.data)
                k    = (yield dut.tx.char_is_k)
                trace.extend([((data >> 8*n) & 0xff, (k >> n) & 1) for n in range(4)])
                yield
        run_simulation(dut, [generator(), monitor()])
        idle = [(K(28, 5), 1), (D(5, 6), 0), (K(28, 5), 1), (D(16, 2), 0)]
        words = [trace[i:i + 4] for i in range(0, len(trace), 4)]
        idles = [w for w in words if w[0] == (K(28, 5), 1)]
        self.assertGreater(len(idles), 4)
        for w in idles:
            self.assertEqual(w, idle)
        ends = [i for i, cg in enumerate(trace) if cg == (K(29, 7), 1)]
        self.assertEqual(len(ends), 4)
        for i in ends:
            self.assertEqual(trace[i + 1], (K(23, 7), 1))
            nxt = i + 2 if (i + 1) % 2 else i + 3
            if nxt == i + 3:
                self.assertEqual(trace[i + 2], (K(23, 7), 1))
            self.assertEqual(nxt % 2, 0)
            self.assertEqual(trace[nxt], (K(28, 5), 1))

    def test_rx_error_ends_packet(self):
        # An invalid K-character inside a packet ends it with an error (no repeated words) and the
        # following packets are received normally.
        dut    = PCS32LoopbackDUT(0, 0)
        frames = frames_for_all_lengths()[:3]
        rx_b, err_b, rx_a, err_a = [], [], [], []
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            yield from send_frames(dut.a.sink, frames, gap=8)
            for _ in range(64):
                yield
        def corrupter():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(10):
                yield
            yield dut.ab.corrupt.eq(1)
            yield
            yield dut.ab.corrupt.eq(0)
        run_simulation(dut, {
                "a_tx": generator(),
                "b_rx": [corrupter(), receive_frames(dut.b.source, rx_b, err_b)],
                "a_rx": receive_frames(dut.a.source, rx_a, err_a),
            }, clocks=PCS32_CLOCKS)
        self.assertEqual(err_b, [0])
        self.assertEqual(len(rx_b), len(frames))
        self.assertLess(len(rx_b[0]), len(frames[0]))
        self.assertEqual(rx_b[1:], frames[1:])

    def test_link_partner_restart(self):
        # The link partner restarting autonegotiation after link up takes the link down and both
        # sides negotiate again.
        dut  = PCS32LoopbackDUT(0, 2)
        seen = {"down": False}
        def generator():
            yield from wait_link_up(self, [dut.a, dut.b])
            for _ in range(16):
                yield
            yield dut.cd_b_tx.rst.eq(1)
            yield
            yield dut.cd_b_tx.rst.eq(0)
            for _ in range(2000):
                if not (yield dut.a.link_up):
                    seen["down"] = True
                    break
                yield
            yield from wait_link_up(self, [dut.a, dut.b])
        run_simulation(dut, {"a_tx": generator()}, clocks=PCS32_CLOCKS)
        self.assertTrue(seen["down"])

# 8-bit <-> 32-bit ---------------------------------------------------------------------------------

class PCS8To32DUT(LiteXModule):
    """8-bit PCS (fabric 8b/10b, reference) linked to the 32-bit PCS through an 8b/10b model of the
    hard coder: 4 code-groups per 32-bit cycle."""
    def __init__(self, offset=0):
        self.cd_p8  = ClockDomain()
        self.cd_p32 = ClockDomain()
        self.p8  = ClockDomainsRenamer({"eth_tx": "p8", "eth_rx": "p8"})(
            PCS(lsb_first=True, sgmii=False, eth_tx_clk_freq=125e6,
                check_period=1024/125e6, breaklink_time=64/125e6, more_ack_time=64/125e6))
        self.p32 = ClockDomainsRenamer({"eth_tx": "p32", "eth_rx": "p32"})(
            PCS(dw=32, eth_tx_clk_freq=31.25e6, **PCS_TIMINGS))

        # # #

        # 8-bit TX -> 32-bit RX: decode and gather 4 code-groups.
        self.decoder = decoder = ClockDomainsRenamer("p8")(Decoder(lsb_first=True))
        gather_data = Signal(32)
        gather_k    = Signal(4)
        gather_err  = Signal(4)
        count       = Signal(2, reset=offset)
        word_data   = Signal(32)
        word_k      = Signal(4)
        word_err    = Signal(4)
        self.comb += [
            decoder.input.eq(self.p8.tbi_tx),
            decoder.ce.eq(1),
        ]
        self.sync.p8 += [
            count.eq(count + 1),
            gather_data.eq(Cat(gather_data[8:], decoder.d)),
            gather_k.eq(Cat(gather_k[1:], decoder.k)),
            gather_err.eq(Cat(gather_err[1:], decoder.invalid)),
            If(count == 0,
                word_data.eq(gather_data),
                word_k.eq(gather_k),
                word_err.eq(gather_err),
            )
        ]
        self.comb += [
            self.p32.rx.data.eq(word_data),
            self.p32.rx.char_is_k.eq(word_k),
            self.p32.rx.table_err.eq(word_err),
        ]

        # 32-bit TX -> 8-bit RX: serialize 4 code-groups and encode.
        self.encoder = encoder = ClockDomainsRenamer("p8")(Encoder(lsb_first=True))
        lane      = Signal(2)
        ser_data  = Signal(32)
        ser_k     = Signal(4)
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


class TestPCS8bTo32bInterop(unittest.TestCase):
    def check(self, offset):
        dut    = PCS8To32DUT(offset)
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
                "p8" : [p8_tx(), receive_frames(dut.p8.source, rx8, err8, nbytes=1)],
            }, clocks={"p8": 4, "p32": 16})
        self.assertEqual(rx8,  frames)
        self.assertEqual(rx32, frames)
        self.assertEqual(err8 + err32, [])

    def test_interop(self):
        for offset in range(4):
            with self.subTest(offset=offset):
                self.check(offset)

# PHY ----------------------------------------------------------------------------------------------

class CCSerDesLoopback:
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
    def test_loopback_autonegotiates_and_transfers_packets(self):
        dut = GateMate_1000BASEX(sys_clk_freq=1e3, with_csr=False, pcs_kwargs=PCS_TIMINGS)
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
            },
            clocks={"sys": 20, "eth_tx": 10, "eth_rx": 10},
            special_overrides={Instance: CCSerDesLoopback()})
        self.assertEqual(rx, frames)
        self.assertEqual(err, [])


if __name__ == "__main__":
    unittest.main()
