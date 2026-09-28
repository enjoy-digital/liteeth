#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen.sim import *

from liteeth.phy.agilex_rgmii import LiteEthRGMIIRXDatapath


def rgmii_samples(data, errors, shifted=False):
    # Each tuple is a nibble and its RX_CTL sample, in wire order.
    samples = [(0, 0)] if shifted else []
    for value, error in zip(data, errors):
        samples += [(value & 0xf, 1), (value >> 4, 1 ^ error)]
    if len(samples) % 2:
        samples.append((0, 0))
    return [(lo | (hi << 4), ctl_lo | (ctl_hi << 1))
        for (lo, ctl_lo), (hi, ctl_hi) in zip(samples[::2], samples[1::2])]


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
            yield dut.rx_ctl_raw.eq(0b10)
            yield dut.rx_data_raw.eq(0x50)
            yield
            yield
            self.assertEqual((yield dut.source.valid), 0)
            self.assertEqual((yield dut.source.error), 0)

        run_simulation(dut, generator())

    def test_complete_frames_preserve_data_errors_and_boundaries(self):
        data = [0x55, 0x55, 0xd5, 0x12, 0x34, 0x56]
        for shifted in (False, True):
            for error_byte in (None, 0, 2, 3, len(data) - 1):
                with self.subTest(shifted=shifted, error_byte=error_byte):
                    dut = LiteEthRGMIIRXDatapath()
                    errors = [int(i == error_byte) for i in range(len(data))]
                    rows = [(0, 0)]*4 + rgmii_samples(data, errors, shifted) + [(0, 0)]*4
                    # Follow with a clean frame using the opposite half-cycle alignment.
                    rows += rgmii_samples(data, [0]*len(data), not shifted) + [(0, 0)]*4
                    observed = []

                    def driver():
                        for value, control in rows:
                            yield dut.rx_data_raw.eq(value)
                            yield dut.rx_ctl_raw.eq(control)
                            yield
                        for _ in range(4):
                            yield

                    @passive
                    def monitor():
                        while True:
                            if (yield dut.source.valid):
                                observed.append(((yield dut.source.data),
                                    (yield dut.source.error), (yield dut.source.last)))
                            yield

                    run_simulation(dut, [driver(), monitor()])
                    expected = [(value, int(error_byte is not None and i >= error_byte),
                        int(i == len(data) - 1)) for i, value in enumerate(data)]
                    expected += [(value, 0, int(i == len(data) - 1)) for i, value in enumerate(data)]
                    self.assertEqual(observed, expected)

    def test_idle_error_does_not_start_a_frame(self):
        dut = LiteEthRGMIIRXDatapath()

        def generator():
            # RX_DV=0, RX_ER=1 is an out-of-frame indication, not a byte with an error.
            yield dut.rx_ctl_raw.eq(0b10)
            yield dut.rx_data_raw.eq(0x0e)
            for _ in range(5):
                yield
                self.assertEqual((yield dut.source.valid), 0)
                self.assertEqual((yield dut.source.last), 0)

        run_simulation(dut, generator())


if __name__ == "__main__":
    unittest.main()
