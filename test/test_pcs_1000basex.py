#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *
from litex.gen import LiteXContext, LiteXModule
from litex.gen.sim import run_simulation
from litex.soc.cores.code_8b10b import K, D, Encoder

from liteeth.phy.pcs_1000basex import (
    PCS,
    PCSGearbox,
    PCSSGMIITimer,
    PCSRX,
    PCSRX4,
    PCSTX,
    PCSTX4,
    SGMII_10MBPS_SPEED,
    SGMII_100MBPS_SPEED,
    SGMII_1000MBPS_SPEED,
)


# Helpers ------------------------------------------------------------------------------------------

class PCSLoopbackDUT(LiteXModule):
    def __init__(self, lsb_first=False):
        self.pcs_a = PCS(
            lsb_first=lsb_first,
            check_period=32/125e6,
            breaklink_time=1/125e6,
            more_ack_time=1/125e6,
            sgmii_ack_time=1/125e6,
        )
        self.pcs_b = PCS(
            lsb_first=lsb_first,
            check_period=32/125e6,
            breaklink_time=1/125e6,
            more_ack_time=1/125e6,
            sgmii_ack_time=1/125e6,
        )

        self.comb += [
            self.pcs_a.tbi_rx.eq(self.pcs_b.tbi_tx),
            self.pcs_b.tbi_rx.eq(self.pcs_a.tbi_tx),
            self.pcs_a.tbi_rx_ce.eq(1),
            self.pcs_b.tbi_rx_ce.eq(1),
        ]


class PCSRXEncodedDUT(LiteXModule):
    def __init__(self, lsb_first=False):
        self.rx  = PCSRX(lsb_first=lsb_first)
        self.enc = Encoder(lsb_first=lsb_first)
        self.comb += self.rx.decoder.input.eq(self.enc.output[0])


class PCSGearboxDUT(LiteXModule):
    def __init__(self):
        self.clock_domains.cd_eth_tx      = ClockDomain("eth_tx")
        self.clock_domains.cd_eth_tx_half = ClockDomain("eth_tx_half")
        self.clock_domains.cd_eth_rx      = ClockDomain("eth_rx")
        self.clock_domains.cd_eth_rx_half = ClockDomain("eth_rx_half")
        self.gearbox = PCSGearbox()


class PCS4LoopbackDUT(LiteXModule):
    def __init__(self, phase=0):
        self.tx = PCSTX4()
        self.rx = PCSRX4()

        raw     = Cat(*self.tx.encoder.output)
        raw_d   = Signal(40)
        shifted = Signal(40)
        self.sync += raw_d.eq(raw)
        if phase == 0:
            self.comb += shifted.eq(raw)
        else:
            # Move code group zero to the requested RX lane while preserving
            # the continuous serial order across 40-bit word boundaries.
            self.comb += shifted.eq(Cat(
                raw_d[10*(4 - phase):],
                raw[:10*(4 - phase)],
            ))
        self.comb += self.rx.input.eq(shifted)


class PCS4AutonegLoopbackDUT(LiteXModule):
    def __init__(self):
        kwargs = dict(
            dw=32,
            check_period=16/156.25e6,
            breaklink_time=1/156.25e6,
            more_ack_time=1/156.25e6,
            sgmii_ack_time=1/156.25e6,
            eth_tx_clk_freq=156.25e6,
        )
        self.pcs_a = PCS(**kwargs)
        self.pcs_b = PCS(**kwargs)
        self.comb += [
            self.pcs_a.tbi_rx.eq(self.pcs_b.tbi_tx),
            self.pcs_b.tbi_rx.eq(self.pcs_a.tbi_tx),
        ]


# Test PCS Gearbox ---------------------------------------------------------------------------------

class TestPCSGearbox(unittest.TestCase):
    def test_tx_packs_two_10b_symbols_to_half_rate_word(self):
        dut = PCSGearboxDUT()
        observed = []

        def eth_tx_generator():
            for data in range(1, 9):
                yield dut.gearbox.tx_data.eq(data)
                yield

        def eth_tx_half_generator():
            for _ in range(6):
                yield
                observed.append((yield dut.gearbox.tx_data_half))

        run_simulation(dut, {
            "eth_tx":      [eth_tx_generator()],
            "eth_tx_half": [eth_tx_half_generator()],
        }, clocks={
            "eth_tx":      10,
            "eth_tx_half": 20,
            "eth_rx":      10,
            "eth_rx_half": 20,
        })
        self.assertEqual(observed[1:5], [
            (2 << 10) | 1,
            (4 << 10) | 3,
            (6 << 10) | 5,
            (8 << 10) | 7,
        ])

    def test_rx_unpacks_half_rate_word_to_two_10b_symbols(self):
        dut = PCSGearboxDUT()
        observed = []

        def eth_rx_half_generator():
            for lo, hi in [(1, 2), (3, 4), (5, 6)]:
                yield dut.gearbox.rx_data_half.eq((hi << 10) | lo)
                yield

        def eth_rx_generator():
            for _ in range(8):
                yield
                observed.append((yield dut.gearbox.rx_data))

        run_simulation(dut, {
            "eth_rx":      [eth_rx_generator()],
            "eth_rx_half": [eth_rx_half_generator()],
        }, clocks={
            "eth_tx":      10,
            "eth_tx_half": 20,
            "eth_rx":      10,
            "eth_rx_half": 20,
        })
        self.assertEqual(observed[1:7], [2, 1, 4, 3, 6, 5])


# Test Four-Symbol PCS ----------------------------------------------------------------------------

class TestPCS4(unittest.TestCase):
    @staticmethod
    def packet_words(data):
        for offset in range(0, len(data), 4):
            chunk = data[offset:offset + 4]
            yield (
                sum(value << (8*byte) for byte, value in enumerate(chunk)),
                offset + 4 >= len(data),
                1 << (len(chunk) - 1),
            )

    def check_loopback(self, phase, length):
        dut      = PCS4LoopbackDUT(phase=phase)
        expected = [0x55] + [((17*n) + 3) & 0xff for n in range(1, length)]
        received = []

        def tx_generator():
            for _ in range(16):
                yield
            for data, last, last_be in self.packet_words(expected):
                yield dut.tx.sink.data.eq(data)
                yield dut.tx.sink.last.eq(last)
                yield dut.tx.sink.last_be.eq(last_be)
                yield dut.tx.sink.valid.eq(1)
                yield
                while not (yield dut.tx.sink.ready):
                    yield
                yield dut.tx.sink.valid.eq(0)
                yield dut.tx.sink.last.eq(0)
                yield dut.tx.sink.last_be.eq(0)
            for _ in range(64):
                yield

        def rx_generator():
            yield dut.rx.source.ready.eq(1)
            for cycle in range(256):
                if cycle > 16:
                    self.assertEqual((yield dut.rx.code_error), 0)
                    self.assertEqual((yield dut.rx.disparity_error), 0)
                if (yield dut.rx.source.valid):
                    data    = (yield dut.rx.source.data)
                    last    = (yield dut.rx.source.last)
                    last_be = (yield dut.rx.source.last_be)
                    count   = last_be.bit_length() if last else 4
                    received.extend((data >> (8*byte)) & 0xff for byte in range(count))
                    self.assertEqual((yield dut.rx.source.error), 0)
                    if last:
                        return
                yield
            self.fail("four-symbol PCS loopback timed out")

        run_simulation(dut, [tx_generator(), rx_generator()])
        self.assertEqual(received, expected)

    def test_all_symbol_phases(self):
        for phase in range(4):
            with self.subTest(phase=phase):
                self.check_loopback(phase=phase, length=64)

    def test_all_last_byte_positions(self):
        for length in range(61, 65):
            with self.subTest(length=length):
                self.check_loopback(phase=0, length=length)

    def test_pcs_32bit_interface(self):
        dut = PCS(
            dw=32,
            check_period=32/156.25e6,
            breaklink_time=1/156.25e6,
            more_ack_time=1/156.25e6,
            sgmii_ack_time=1/156.25e6,
        )
        self.assertEqual(len(dut.tbi_tx), 40)
        self.assertEqual(len(dut.tbi_rx), 40)
        self.assertEqual(len(dut.sink.data), 32)
        self.assertEqual(len(dut.source.data), 32)

    def test_parallel_pcs_autonegotiation(self):
        dut = PCS4AutonegLoopbackDUT()

        def generator():
            for _ in range(120):
                yield
            self.assertEqual((yield dut.pcs_a.link_up), 1)
            self.assertEqual((yield dut.pcs_b.link_up), 1)
            self.assertEqual((yield dut.pcs_a.lp_abi.o), 0x4020)
            self.assertEqual((yield dut.pcs_b.lp_abi.o), 0x4020)

        run_simulation(dut, {"eth_tx": [generator()]}, clocks={
            "eth_tx": 10,
            "eth_rx": 10,
        })

    def test_rejects_unsupported_width(self):
        with self.assertRaisesRegex(ValueError, "8 or 32"):
            PCS(dw=16)


# Test PCS SGMII Timer -----------------------------------------------------------------------------

class TestPCSSGMIITimer(unittest.TestCase):
    def check_timer_period(self, speed, period):
        dut = PCSSGMIITimer(speed=Signal(2, reset=speed))
        done_cycles = []

        def generator():
            yield dut.enable.eq(1)
            for cycle in range(period * 3 + 2):
                if (yield dut.done):
                    done_cycles.append(cycle)
                yield

        run_simulation(dut, generator())
        self.assertGreaterEqual(len(done_cycles), 3)
        self.assertEqual(done_cycles[1] - done_cycles[0], period)
        self.assertEqual(done_cycles[2] - done_cycles[1], period)

    def test_10mbps_period(self):
        self.check_timer_period(SGMII_10MBPS_SPEED, 100)

    def test_100mbps_period(self):
        self.check_timer_period(SGMII_100MBPS_SPEED, 10)

    def test_1000mbps_period(self):
        self.check_timer_period(SGMII_1000MBPS_SPEED, 1)


# Test PCS Autoneg Config --------------------------------------------------------------------------

class TestPCSAutonegConfig(unittest.TestCase):
    def make_dut(self):
        return PCS(
            check_period=8/125e6,
            breaklink_time=1/125e6,
            more_ack_time=1/125e6,
            sgmii_ack_time=1/125e6,
        )

    def test_1000basex_advertises_full_duplex(self):
        dut = self.make_dut()

        def generator():
            yield dut.config_empty.eq(0)
            yield dut.lp_abi.o.eq(0x0020)
            yield
            yield
            self.assertEqual((yield dut.tx.config_reg), 0x0020)
            self.assertEqual((yield dut.tx.sgmii_speed), SGMII_1000MBPS_SPEED)
            self.assertEqual((yield dut.rx.sgmii_speed), SGMII_1000MBPS_SPEED)

        run_simulation(dut, generator(), clocks={
            "sys":    10,
            "eth_tx": 10,
            "eth_rx": 10,
        })

    def test_with_csr_keeps_autoneg_and_csr_fsms(self):
        class Top:
            sys_clk_freq = int(100e6)

        old_top = LiteXContext.top
        LiteXContext.top = Top()
        try:
            dut = PCS(with_csr=True)
        finally:
            LiteXContext.top = old_top

        self.assertIn("AUTONEG-BREAKLINK", dut.fsm.actions)
        self.assertIn("RUNNING", dut.fsm.actions)
        self.assertIn("DOWN", dut.csr_fsm.actions)
        self.assertIn("UP", dut.csr_fsm.actions)

    def test_sgmii_advertises_speed_and_duplex(self):
        dut = self.make_dut()

        def generator():
            yield dut.config_empty.eq(0)
            yield dut.link_up.eq(1)
            for speed in [SGMII_10MBPS_SPEED, SGMII_100MBPS_SPEED, SGMII_1000MBPS_SPEED]:
                yield dut.lp_abi.o.eq((1 << 15) | (speed << 10) | 1)
                for _ in range(2):
                    yield dut.lp_abi.o.eq((1 << 15) | (speed << 10) | 1)
                    yield
                self.assertEqual((yield dut.is_sgmii), 1)
                self.assertEqual((yield dut.linkdown), 0)
                self.assertEqual((yield dut.tx.sgmii_speed), speed)
                self.assertEqual((yield dut.tx.config_reg),
                    (1 << 12) | (speed << 10) | 1)

        run_simulation(dut, generator(), clocks={
            "sys":    10,
            "eth_tx": 10,
            "eth_rx": 10,
        })

    def test_sgmii_remote_link_down_is_not_advertised_up(self):
        dut = self.make_dut()

        def generator():
            yield dut.config_empty.eq(0)
            yield dut.link_up.eq(1)
            yield dut.lp_abi.o.eq((SGMII_1000MBPS_SPEED << 10) | 1)
            yield
            yield
            self.assertEqual((yield dut.linkdown), 1)
            self.assertEqual((yield dut.tx.config_reg[15]), 0)

        run_simulation(dut, generator(), clocks={
            "sys":    10,
            "eth_tx": 10,
            "eth_rx": 10,
        })

    def test_sgmii_reserved_speed_forces_link_down_and_clamps_timer_speed(self):
        dut = self.make_dut()

        def generator():
            yield dut.config_empty.eq(0)
            yield dut.lp_abi.o.eq((1 << 15) | (0b11 << 10) | 1)
            yield dut.lp_abi.i.eq((0b11 << 10) | 1)
            yield
            yield
            self.assertEqual((yield dut.is_sgmii), 1)
            self.assertEqual((yield dut.linkdown), 1)
            self.assertEqual((yield dut.tx.sgmii_speed), SGMII_1000MBPS_SPEED)
            self.assertEqual((yield dut.rx.sgmii_speed), SGMII_1000MBPS_SPEED)
            self.assertEqual((yield dut.tx.config_reg[10:12]), SGMII_1000MBPS_SPEED)

        run_simulation(dut, generator(), clocks={
            "sys":    10,
            "eth_tx": 10,
            "eth_rx": 10,
        })

    def test_two_1000basex_pcs_autonegotiate_to_link_up(self):
        for lsb_first in [False, True]:
            dut = PCSLoopbackDUT(lsb_first=lsb_first)

            def generator():
                for _ in range(160):
                    if (yield dut.pcs_a.link_up) and (yield dut.pcs_b.link_up):
                        self.assertEqual((yield dut.pcs_a.is_sgmii), 0)
                        self.assertEqual((yield dut.pcs_b.is_sgmii), 0)
                        return
                    yield
                self.fail("1000BASE-X PCS pair did not complete autonegotiation")

            run_simulation(dut, generator(), clocks={
                "sys":    10,
                "eth_tx": 10,
                "eth_rx": 10,
            })

    def test_two_1000basex_pcs_autonegotiate_with_asymmetric_clocks(self):
        dut = PCSLoopbackDUT()

        def generator():
            for _ in range(260):
                if (yield dut.pcs_a.link_up) and (yield dut.pcs_b.link_up):
                    self.assertEqual((yield dut.pcs_a.is_sgmii), 0)
                    self.assertEqual((yield dut.pcs_b.is_sgmii), 0)
                    return
                yield
            self.fail("1000BASE-X PCS pair did not complete autonegotiation")

        run_simulation(dut, generator(), clocks={
            "sys":     7,
            "eth_tx": 10,
            "eth_rx": 11,
        })

    def test_autoneg_timers_scale_with_eth_tx_clk_freq(self):
        state_cycles = {}

        for clk_freq in [125e6, 250e6]:
            dut = PCS(
                check_period=100/125e6,
                breaklink_time=4/125e6,
                more_ack_time=1/125e6,
                sgmii_ack_time=1/125e6,
                eth_tx_clk_freq=clk_freq,
            )

            def generator():
                for cycle in range(20):
                    if (yield dut.fsm.state) == 1:
                        state_cycles[clk_freq] = cycle
                        return
                    yield
                self.fail("PCS did not leave breaklink state")

            run_simulation(dut, generator(), clocks={
                "sys":    10,
                "eth_tx": 10,
                "eth_rx": 10,
            })

        self.assertEqual(state_cycles[250e6] - 1, 2 * (state_cycles[125e6] - 1))


# Test PCS TX --------------------------------------------------------------------------------------

class TestPCSTX(unittest.TestCase):
    def test_config_words_alternate_and_latch_config_register_bytes(self):
        dut = PCSTX()
        symbols = []

        def generator():
            yield dut.config_valid.eq(1)
            yield dut.config_reg.eq(0x1234)
            for _ in range(8):
                yield
                symbols.append(((yield dut.encoder.k[0]), (yield dut.encoder.d[0])))

        run_simulation(dut, generator())
        self.assertEqual(symbols[1:5], [
            (1, K(28, 5)),
            (0, D(21, 5)),
            (0, 0x34),
            (0, 0x12),
        ])
        self.assertEqual(symbols[5:8], [
            (1, K(28, 5)),
            (0, D(2, 2)),
            (0, 0x34),
        ])

    def check_data_ready_period(self, speed, expected_period):
        dut = PCSTX()
        ready_cycles = []

        def generator():
            yield dut.sgmii_speed.eq(speed)
            yield dut.sink.valid.eq(1)
            yield dut.sink.data.eq(0x5a)
            for cycle in range(expected_period * 3 + 8):
                if (yield dut.sink.ready):
                    ready_cycles.append(cycle)
                yield

        run_simulation(dut, generator())
        self.assertGreaterEqual(len(ready_cycles), 3)
        self.assertEqual(ready_cycles[1] - ready_cycles[0], expected_period)
        self.assertEqual(ready_cycles[2] - ready_cycles[1], expected_period)

    def test_data_ready_is_throttled_for_sgmii_speeds(self):
        self.check_data_ready_period(SGMII_1000MBPS_SPEED, 1)
        self.check_data_ready_period(SGMII_100MBPS_SPEED, 10)
        self.check_data_ready_period(SGMII_10MBPS_SPEED, 100)


# Test PCS RX --------------------------------------------------------------------------------------

class TestPCSRX(unittest.TestCase):
    def test_config_ordered_sets_decode_config_register(self):
        for lsb_first in [False, True]:
            dut = PCSRXEncodedDUT(lsb_first=lsb_first)
            symbols = [(1, K(28, 5)), (0, D(21, 5)), (0, 0x34), (0, 0x12)] * 4
            config_seen = []
            ci_seen = []

            def generator():
                for cycle, (k, data) in enumerate(symbols):
                    yield dut.enc.k[0].eq(k)
                    yield dut.enc.d[0].eq(data)
                    yield dut.rx.decoder.ce.eq(cycle > 1)
                    yield
                    if (yield dut.rx.seen_valid_ci):
                        ci_seen.append(cycle)
                    if (yield dut.rx.seen_config_reg):
                        config_seen.append(cycle)
                self.assertGreaterEqual(len(ci_seen), 3)
                self.assertGreaterEqual(len(config_seen), 3)
                self.assertEqual((yield dut.rx.config_reg), 0x1234)

            run_simulation(dut, generator())

    def test_idle_ordered_sets_are_seen_as_valid_ci(self):
        dut = PCSRXEncodedDUT()
        symbols = [(1, K(28, 5)), (0, D(5, 6)), (1, K(28, 5)), (0, D(16, 2))] * 2
        ci_seen = []

        def generator():
            for cycle, (k, data) in enumerate(symbols):
                yield dut.enc.k[0].eq(k)
                yield dut.enc.d[0].eq(data)
                yield dut.rx.decoder.ce.eq(cycle > 1)
                yield
                if (yield dut.rx.seen_valid_ci):
                    ci_seen.append(cycle)
            self.assertGreaterEqual(len(ci_seen), 2)
            self.assertEqual((yield dut.rx.seen_config_reg), 0)

        run_simulation(dut, generator())

    def test_data_packet_decodes_preamble_payload_and_last(self):
        for lsb_first in [False, True]:
            dut = PCSRXEncodedDUT(lsb_first=lsb_first)
            symbols = [
                (1, K(27, 7)),
                (0, 0x11),
                (0, 0x22),
                (0, 0x33),
                (1, K(29, 7)),
                (1, K(23, 7)),
                (1, K(28, 5)),
                (0, D(5, 6)),
            ]
            received = []

            def generator():
                yield dut.rx.source.ready.eq(1)
                yield dut.rx.sgmii_speed.eq(SGMII_1000MBPS_SPEED)
                for cycle, (k, data) in enumerate(symbols + [(1, K(28, 5)), (0, D(5, 6))]):
                    yield dut.enc.k[0].eq(k)
                    yield dut.enc.d[0].eq(data)
                    yield dut.rx.decoder.ce.eq(cycle > 1)
                    yield
                    if (yield dut.rx.source.valid):
                        received.append((
                            (yield dut.rx.source.data),
                            (yield dut.rx.source.last),
                            (yield dut.rx.source.error),
                        ))
                self.assertEqual(received, [
                    (0x55, 0, 0),
                    (0x11, 0, 0),
                    (0x22, 0, 0),
                    (0x33, 1, 0),
                ])

            run_simulation(dut, generator())

    def test_unexpected_k_symbol_in_data_reports_error(self):
        dut = PCSRXEncodedDUT()
        received_errors = []

        def generator():
            yield dut.rx.source.ready.eq(1)
            yield dut.rx.sgmii_speed.eq(SGMII_1000MBPS_SPEED)
            symbols = [
                (1, K(27, 7)),
                (0, 0x11),
                (1, K(28, 5)),
                (1, K(28, 5)),
                (1, K(28, 5)),
                (1, K(28, 5)),
            ]
            for cycle, (k, data) in enumerate(symbols):
                yield dut.enc.k[0].eq(k)
                yield dut.enc.d[0].eq(data)
                yield dut.rx.decoder.ce.eq(cycle > 1)
                yield
                if (yield dut.rx.source.valid) and (yield dut.rx.source.error):
                    received_errors.append((
                        (yield dut.rx.source.last),
                        (yield dut.rx.source.error),
                    ))
            self.assertEqual(received_errors, [(1, 1)])

        run_simulation(dut, generator())


if __name__ == "__main__":
    unittest.main()
