#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen.sim import *

from liteeth.phy.agilex_rgmii import LiteEthRGMIIRXDatapath


class TestAgilexRGMIIRX(unittest.TestCase):
    def test_aligned_control_error(self):
        dut = LiteEthRGMIIRXDatapath()

        def generator():
            yield dut.rx_ctl_raw.eq(0b11)
            yield dut.rx_data_raw.eq(0x5a)
            yield
            yield
            self.assertEqual((yield dut.source.valid), 1)
            self.assertEqual((yield dut.source.data), 0x5a)
            self.assertEqual((yield dut.source.error), 0)

            yield dut.rx_ctl_raw.eq(0b01)
            yield dut.rx_data_raw.eq(0xa5)
            yield
            yield
            self.assertEqual((yield dut.source.valid), 1)
            self.assertEqual((yield dut.source.data), 0xa5)
            self.assertEqual((yield dut.source.error), 1)

        run_simulation(dut, generator())

    def test_unaligned_start_is_discarded(self):
        dut = LiteEthRGMIIRXDatapath()

        def generator():
            # Select half-cycle alignment and discard the incomplete first byte.
            yield dut.rx_ctl_raw.eq(0b01)
            yield dut.rx_data_raw.eq(0xb0)
            yield
            yield
            self.assertEqual((yield dut.source.valid), 0)
            self.assertEqual((yield dut.source.error), 0)

        run_simulation(dut, generator())


if __name__ == "__main__":
    unittest.main()
