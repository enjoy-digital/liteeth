#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *

from liteeth.mac.gap import LiteEthMACGap


class TestMACGap(unittest.TestCase):
    def check_gap_cycles(self, cycles):
        gap_cycles = Signal(max=32, reset=cycles)
        dut        = LiteEthMACGap(dw=8, cycles=gap_cycles)
        observed   = dict(low_cycles=0)

        def generator():
            yield dut.source.ready.eq(1)

            # Send a single one-byte packet.
            yield dut.sink.valid.eq(1)
            yield dut.sink.last.eq(1)
            yield dut.sink.last_be.eq(1)
            while not (yield dut.sink.ready):
                yield
            yield

            yield dut.sink.valid.eq(0)
            yield dut.sink.last.eq(0)
            yield dut.sink.last_be.eq(0)

            # The sink must remain stalled for the configured gap cycles.
            for _ in range(cycles):
                self.assertEqual((yield dut.sink.ready), 0)
                observed["low_cycles"] += 1
                yield

            self.assertEqual((yield dut.sink.ready), 1)

        run_simulation(dut, generator())
        self.assertEqual(observed["low_cycles"], cycles)

    def test_dynamic_gap_cycles(self):
        for cycles in [1, 3, 12, 24]:
            with self.subTest(cycles=cycles):
                self.check_gap_cycles(cycles)


if __name__ == "__main__":
    unittest.main()
