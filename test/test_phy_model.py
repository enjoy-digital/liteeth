#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *

from test.model import phy
from test.stream_helpers import Packet

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw):
        self.phy_model = phy.PHY(dw)
        self.comb += self.phy_model.source.connect(self.phy_model.sink)

# Generator ----------------------------------------------------------------------------------------

def main_generator(dut, dw):
    tc = unittest.TestCase()

    for length in [1, 2, 3, 4, 5, 7, 8, 9, 17]:
        packet = Packet(range(length))
        dut.phy_model.send(packet)
        yield from dut.phy_model.receive()
        tc.assertEqual(list(dut.phy_model.packet), list(packet),
            f"PHY model roundtrip failed for dw={dw}, length={length}")

# Test PHY Model -----------------------------------------------------------------------------------

class TestPHYModel(unittest.TestCase):
    def run_test(self, dw):
        dut = DUT(dw)
        run_simulation(dut, [
            main_generator(dut, dw),
            dut.phy_model.phy_source.generator(),
            dut.phy_model.phy_sink.generator(),
        ])

    def test_dw_8(self):
        self.run_test(8)

    def test_dw_32(self):
        self.run_test(32)

    def test_dw_64(self):
        self.run_test(64)
