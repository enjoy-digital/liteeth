#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace

from migen import ClockDomain, Signal

from litex.gen.sim import run_simulation

from liteeth.phy.serial.baser.wrappers.diagnostics import LiteEthBASERPHY


class DiagnosticsDUT(LiteEthBASERPHY):
    def __init__(self, width=32, with_csr=False, with_prbs=True):
        self.cd_sys    = ClockDomain()
        self.cd_eth_tx = ClockDomain()
        self.cd_eth_rx = ClockDomain()
        self.pcs = SimpleNamespace(
            rx_error_count=Signal(7), rx_block_lock=Signal(), rx_high_ber=Signal())
        self.reset            = Signal()
        self.link_up          = Signal()
        self.loopback         = Signal(3)
        self.tx_prbs31_enable = Signal()
        self.rx_prbs31_enable = Signal()
        self.rx_ce            = Signal()
        self.with_prbs        = with_prbs
        if with_prbs:
            self.add_prbs_counter(width, rx_ce=self.rx_ce)
        if with_csr:
            self.add_csr()
            # A SoC's CSR bank normally finalizes and registers these modules.
            for csr in self.get_csrs():
                csr.finalize(32, "big")
                self.submodules += csr


class TestBASERDiagnostics(unittest.TestCase):
    def wait_paused(self, dut, value):
        for _ in range(600):
            if (yield dut.rx_prbs_paused) == value:
                return
            yield
        self.fail(f"Pause acknowledgment did not reach {value}")

    def test_large_increment_saturates_before_truncation(self):
        for width in (1, 4, 6, 7, 32):
            for errors in (0, 1, 15, 64, 66):
                with self.subTest(width=width, errors=errors):
                    dut = DiagnosticsDUT(width)
                    finished = []

                    def receiver():
                        yield dut.rx_prbs31_enable.eq(1)
                        for _ in range(3):
                            yield
                        yield dut.pcs.rx_error_count.eq(errors)
                        yield dut.rx_ce.eq(1)
                        yield
                        yield dut.rx_ce.eq(0)
                        yield
                        finished.append(True)

                    def system():
                        while not finished:
                            yield
                        yield dut.rx_prbs_pause.eq(1)
                        yield from self.wait_paused(dut, 1)
                        self.assertEqual((yield dut.rx_prbs_errors), min(errors, 2**width - 1))

                    run_simulation(dut, {"eth_rx": receiver(), "sys": system()},
                        clocks={"sys": 10, "eth_rx": 14})

    def test_pause_resume_gaps_and_checker_disable(self):
        for rx_period in (6, 14, 26):
            with self.subTest(rx_period=rx_period):
                dut = DiagnosticsDUT(8)
                phase = {"rx": 0, "sys": 0}

                def receiver():
                    yield dut.rx_prbs31_enable.eq(1)
                    for _ in range(3):
                        yield
                    yield dut.pcs.rx_error_count.eq(3)
                    yield dut.rx_ce.eq(1)
                    for _ in range(5):
                        yield
                    yield dut.rx_ce.eq(0)
                    yield dut.pcs.rx_error_count.eq(66)
                    for _ in range(5):
                        yield
                    phase["rx"] = 1
                    while phase["sys"] < 1:
                        yield
                    yield dut.pcs.rx_error_count.eq(5)
                    yield dut.rx_ce.eq(1)
                    for _ in range(3):
                        yield
                    yield dut.rx_ce.eq(0)
                    yield
                    phase["rx"] = 2
                    while phase["sys"] < 2:
                        yield
                    yield dut.rx_prbs31_enable.eq(0)

                def system():
                    while phase["rx"] < 1:
                        yield
                    yield dut.rx_prbs_pause.eq(1)
                    yield from self.wait_paused(dut, 1)
                    for _ in range(20):
                        self.assertEqual((yield dut.rx_prbs_errors), 15)
                        yield
                    yield dut.rx_prbs_pause.eq(0)
                    yield from self.wait_paused(dut, 0)
                    phase["sys"] = 1
                    while phase["rx"] < 2:
                        yield
                    yield dut.rx_prbs_pause.eq(1)
                    yield from self.wait_paused(dut, 1)
                    self.assertEqual((yield dut.rx_prbs_errors), 30)
                    phase["sys"] = 2
                    for _ in range(200):
                        yield
                    self.assertEqual((yield dut.rx_prbs_errors), 0)

                run_simulation(dut, {"eth_rx": receiver(), "sys": system()},
                    clocks={"sys": 10, "eth_rx": rx_period})

    def test_counter_and_snapshot_recover_from_receive_reset(self):
        dut = DiagnosticsDUT(4)

        def system():
            yield dut.rx_prbs31_enable.eq(1)
            yield dut.pcs.rx_error_count.eq(66)
            yield dut.rx_ce.eq(1)
            for _ in range(20):
                yield
            yield dut.rx_prbs_pause.eq(1)
            yield from self.wait_paused(dut, 1)
            self.assertEqual((yield dut.rx_prbs_errors), 15)
            yield dut.rx_ce.eq(0)
            yield dut.cd_eth_rx.rst.eq(1)
            for _ in range(10):
                yield
            yield dut.cd_eth_rx.rst.eq(0)
            for _ in range(400):
                yield
            self.assertEqual((yield dut.rx_prbs_errors), 0)
            self.assertEqual((yield dut.rx_prbs_paused), 1)

        run_simulation(dut, system(), clocks={"sys": 10, "eth_rx": 14})

    def test_csr_layout_and_sampled_error_count(self):
        dut = DiagnosticsDUT(with_csr=True)
        fields = dut._status.fields.fields
        self.assertEqual({f.name: (f.offset, f.size) for f in fields}, {
            "block_lock"       : (0, 1),
            "high_ber"         : (1, 1),
            "link_up"          : (2, 1),
            "error_count"      : (3, 7),
            "block_lock_lost"  : (10, 1),
            "high_ber_latched" : (11, 1),
            "prbs_paused"      : (12, 1),
        })
        self.assertEqual(dut._control.size, 6)

        def receiver():
            for _ in range(20):
                yield dut.pcs.rx_error_count.eq(21)
                yield
                yield dut.pcs.rx_error_count.eq(42)
                yield
            yield dut.pcs.rx_error_count.eq(21)

        def system():
            yield dut._control.storage.eq((1 << 4) | (1 << 5))
            for _ in range(160):
                self.assertIn((yield dut._status.fields.error_count), (0, 21, 42))
                yield
            self.assertEqual((yield dut._status.fields.error_count), 21)
            self.assertEqual((yield dut._status.fields.prbs_paused), 1)

        run_simulation(dut, {"eth_rx": receiver(), "sys": system()},
            clocks={"sys": 10, "eth_rx": 14, "eth_tx": 18})

    def test_without_prbs_and_invalid_counter_width(self):
        dut = DiagnosticsDUT(with_prbs=False, with_csr=True)
        self.assertFalse(hasattr(dut, "_rx_prbs_errors"))
        self.assertEqual(dut._status.fields.prbs_paused.offset, 12)
        with self.assertRaisesRegex(ValueError, "width"):
            DiagnosticsDUT(width=0)
