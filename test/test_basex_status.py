#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace
from unittest import mock

from migen import ClockDomain, Signal
from migen.genlib.cdc import MultiReg

from litex.gen import LiteXContext, LiteXModule
from litex.gen.sim import run_simulation

from liteeth.phy.serial.basex.pcs import PCS


class StatusDUT(LiteXModule):
    def __init__(self):
        self.cd_sys      = ClockDomain()
        self.cd_eth_tx   = ClockDomain()
        self.cd_eth_rx   = ClockDomain()
        self.link_up     = Signal()
        self.is_sgmii    = Signal()
        self.lp_abi      = SimpleNamespace(i=Signal(16))
        self.sys_clk_freq = 8
        PCS.add_csr(self)


class TestBASEXStatus(unittest.TestCase):
    def test_csr_construction_without_global_top(self):
        with mock.patch.object(LiteXContext, "top", None):
            pcs = PCS(with_csr=True, sys_clk_freq=100e6)
            self.assertEqual(pcs.status.size, 32)
            pcs = PCS(sys_clk_freq=100e6)
            pcs.add_csr()
            with self.assertRaisesRegex(ValueError, "sys_clk_freq"):
                PCS(with_csr=True)

    def test_legacy_context_clock_and_explicit_add_csr(self):
        with mock.patch.object(LiteXContext, "top", SimpleNamespace(sys_clk_freq=100e6)):
            self.assertTrue(hasattr(PCS(with_csr=True), "csr_fsm"))
        with mock.patch.object(LiteXContext, "top", None):
            pcs = PCS()
            pcs.add_csr(sys_clk_freq=100e6)

    def test_status_uses_cdc_helpers(self):
        dut = StatusDUT()
        synchronizers = [s for s in dut.get_fragment().specials if isinstance(s, MultiReg)]
        for source, destination in (
            (dut.link_up, dut.status.fields.link_up),
            (dut.is_sgmii, dut.status.fields.is_sgmii),
        ):
            self.assertTrue(any(s.i is source and s.o is destination and s.odomain == "sys"
                and s.n >= 2 for s in synchronizers))

    def test_status_and_debounced_event_with_independent_clocks(self):
        for tx_period in (6, 14):
            with self.subTest(tx_period=tx_period):
                dut = StatusDUT()
                samples = []

                def transmitter():
                    for _ in range(5):
                        yield
                    yield dut.link_up.eq(1)
                    yield dut.is_sgmii.eq(1)
                    for _ in range(30):
                        yield
                    yield dut.link_up.eq(0)
                    yield dut.is_sgmii.eq(0)
                    for _ in range(15):
                        yield

                def system():
                    for _ in range(100):
                        samples.append(((yield dut.link_up), (yield dut.status.fields.link_up),
                            (yield dut.status.fields.is_sgmii), (yield dut.ev.link.trigger)))
                        yield

                run_simulation(dut, {"eth_tx": transmitter(), "sys": system()},
                    clocks={"sys": 10, "eth_tx": tx_period, "eth_rx": 18})
                first_raw   = next(i for i, row in enumerate(samples) if row[0])
                first_sync  = next(i for i, row in enumerate(samples) if row[1])
                first_event = next(i for i, row in enumerate(samples) if row[3])
                self.assertGreaterEqual(first_sync - first_raw, 1)
                self.assertGreaterEqual(first_event - first_sync, 8)
                self.assertTrue(all(link == sgmii for _, link, sgmii, _ in samples))
                self.assertEqual(samples[-1], (0, 0, 0, 0))
