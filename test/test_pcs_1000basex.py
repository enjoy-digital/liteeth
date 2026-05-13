#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *
from litex.gen import LiteXContext
from litex.gen.sim import run_simulation
from litex.soc.cores.code_8b10b import K, D

from liteeth.phy.pcs_1000basex import (
    PCS,
    PCSSGMIITimer,
    PCSTX,
    SGMII_10MBPS_SPEED,
    SGMII_100MBPS_SPEED,
    SGMII_1000MBPS_SPEED,
)


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

        run_simulation(dut, generator(), clocks={"sys": 10, "eth_tx": 10, "eth_rx": 10})

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

        run_simulation(dut, generator(), clocks={"sys": 10, "eth_tx": 10, "eth_rx": 10})

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

        run_simulation(dut, generator(), clocks={"sys": 10, "eth_tx": 10, "eth_rx": 10})


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


if __name__ == "__main__":
    unittest.main()
