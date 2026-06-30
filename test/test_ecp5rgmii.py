#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen.sim import *

from liteeth.phy.ecp5rgmii import (
    LiteEthRGMIILinkState,
    LiteEthRGMIIRXDatapath,
    LiteEthRGMIITXClock,
    LiteEthRGMIITXDatapath,
)


class TestECP5RGMIIDynamicSpeed(unittest.TestCase):
    def test_tx_clock_adapts_enable_and_gap_cycles(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIITXClock(link_state=link_state, external_tx_clk=True)

        enable_100m = []
        enable_10m  = []

        def eth_tx_generator():
            yield link_state.link_1G.eq(0)
            yield link_state.link_100M.eq(1)
            yield
            self.assertEqual((yield dut.gap_cycles), 120)
            for _ in range(25):
                enable_100m.append((yield dut.tx_enable))
                yield

            yield link_state.link_100M.eq(0)
            yield link_state.link_10M.eq(1)
            yield
            self.assertEqual((yield dut.gap_cycles), 1200)
            for _ in range(60):
                enable_10m.append((yield dut.tx_enable))
                yield

        run_simulation(dut, {"eth_tx": [eth_tx_generator()]}, clocks={"eth_tx": 10})

        pulses_100m = [i for i, enable in enumerate(enable_100m) if enable]
        pulses_10m  = [i for i, enable in enumerate(enable_10m)  if enable]
        self.assertEqual([b - a for a, b in zip(pulses_100m[-4:], pulses_100m[-3:])], [5, 5, 5])
        self.assertEqual([b - a for a, b in zip(pulses_10m[-2:],  pulses_10m[-1:])],  [50])

    def test_loop_clock_keeps_enable_and_uses_wide_gap(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIITXClock(link_state=link_state, external_tx_clk=False)

        def generator():
            yield link_state.link_1G.eq(1)
            yield
            self.assertEqual((yield dut.tx_enable), 1)
            self.assertEqual((yield dut.rising), 1)
            self.assertEqual((yield dut.falling), 0)
            self.assertEqual((yield dut.gap_cycles), 12)

            yield link_state.link_1G.eq(0)
            yield link_state.link_100M.eq(1)
            yield
            self.assertEqual((yield dut.tx_enable), 1)
            self.assertEqual((yield dut.rising), 1)
            self.assertEqual((yield dut.falling), 0)
            self.assertEqual((yield dut.gap_cycles), 24)

        run_simulation(dut, generator())

    def test_tx_1g_uses_ddr_byte_each_cycle(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIITXDatapath(link_state=link_state)

        def generator():
            yield link_state.link_1G.eq(1)
            yield dut.sink.valid.eq(1)
            yield dut.sink.data.eq(0x5a)
            self.assertEqual((yield dut.sink.ready), 1)
            yield
            yield
            self.assertEqual((yield dut.tx_ctl), 0b11)
            self.assertEqual((yield dut.tx_data), 0x5a)

        run_simulation(dut, generator())

    def test_tx_100m_serializes_byte_over_two_clocks(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIITXDatapath(link_state=link_state)

        def generator():
            yield link_state.link_1G.eq(0)
            yield link_state.link_100M.eq(1)
            yield

            yield dut.sink.valid.eq(1)
            yield dut.sink.data.eq(0xab)
            self.assertEqual((yield dut.sink.ready), 1)
            yield
            yield dut.sink.valid.eq(0)
            yield
            self.assertEqual((yield dut.tx_ctl), 0b11)
            self.assertEqual((yield dut.tx_data), 0xbb)
            self.assertEqual((yield dut.sink.ready), 0)

            yield
            self.assertEqual((yield dut.tx_ctl), 0b11)
            self.assertEqual((yield dut.tx_data), 0xaa)
            self.assertEqual((yield dut.sink.ready), 1)

        run_simulation(dut, generator())

    def test_rx_1g_uses_ddr_byte_each_cycle(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIIRXDatapath(link_state=link_state)

        def generator():
            yield link_state.link_1G.eq(1)
            yield dut.rx_ctl.eq(0b11)
            yield dut.rx_data.eq(0x5a)
            yield
            yield
            self.assertEqual((yield dut.source.valid), 1)
            self.assertEqual((yield dut.source.data), 0x5a)

            yield dut.rx_ctl.eq(0b00)
            yield
            self.assertEqual((yield dut.source.last), 1)

        run_simulation(dut, generator())

    def test_rx_100m_reassembles_two_sdr_nibbles(self):
        link_state = LiteEthRGMIILinkState()
        dut        = LiteEthRGMIIRXDatapath(link_state=link_state)

        def generator():
            yield link_state.link_1G.eq(0)
            yield link_state.link_100M.eq(1)
            yield

            yield dut.rx_ctl.eq(0b01)
            yield dut.rx_data.eq(0x0b)
            yield
            self.assertEqual((yield dut.source.valid), 0)

            yield dut.rx_data.eq(0x0a)
            yield
            self.assertEqual((yield dut.source.valid), 0)

            yield dut.rx_ctl.eq(0b00)
            yield
            self.assertEqual((yield dut.source.valid), 1)
            self.assertEqual((yield dut.source.data), 0xab)
            self.assertEqual((yield dut.source.last), 1)

        run_simulation(dut, generator())


if __name__ == "__main__":
    unittest.main()
