#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.gen import *
from litex.gen.sim import *

from liteeth.common import *
from liteeth.mac.gap import LiteEthMACGap

from liteeth.phy.parallel.mii  import LiteEthPHYMII, LiteEthPHYMIITX
from liteeth.phy.parallel.rmii import LiteEthPHYRMIITX

# Helpers ------------------------------------------------------------------------------------------

def measure_ifg_cycles(tx, pads, gap_cycles, speed=None, frame_length=8, cycles=6000):
    """Send two back-to-back frames through the MAC gap inserter and the PHY TX, return the
    number of cycles tx_en stays low between them."""
    dut = LiteXModule()
    dut.gap = gap = LiteEthMACGap(dw=8, cycles=gap_cycles)
    dut.tx  = tx
    dut.comb += gap.source.connect(tx.sink)

    tx_en = []

    def generator():
        if speed is not None:
            yield tx.speed.eq(speed)
        for frame in range(2):
            for i in range(frame_length):
                yield gap.sink.valid.eq(1)
                yield gap.sink.data.eq(i)
                yield gap.sink.last.eq(i == (frame_length - 1))
                yield
                while not (yield gap.sink.ready):
                    yield
            yield gap.sink.valid.eq(0)

    def monitor():
        for _ in range(cycles):
            tx_en.append((yield pads.tx_en))
            yield

    run_simulation(dut, [generator(), monitor()])
    trace = "".join(str(v) for v in tx_en).strip("0")
    return max(len(low) for low in trace.split("1"))

# Test PHY Inter-Frame Gap -------------------------------------------------------------------------

class TestPHYIFG(unittest.TestCase):
    ifg_bits = 8*eth_interpacket_gap # 96 bit times.

    def test_mii_ifg(self):
        # MII: 4 bits per eth_tx cycle at 10Mbps and 100Mbps.
        pads   = Record([("tx_en", 1), ("tx_data", 4)])
        tx     = LiteEthPHYMIITX(pads)
        cycles = measure_ifg_cycles(tx, pads, gap_cycles=LiteEthPHYMII.tx_gap_cycles)
        self.assertGreaterEqual(4*cycles, self.ifg_bits)

    def test_rmii_ifg(self):
        # RMII: 2 bits per eth_tx cycle at 100Mbps, per 10 cycles at 10Mbps.
        for speed, bits_per_10_cycles in [(1, 20), (0, 2)]:
            with self.subTest(speed=["10Mbps", "100Mbps"][speed]):
                pads   = Record([("tx_en", 1), ("tx_data", 2)])
                tx     = LiteEthPHYRMIITX(pads, ClockSignal())
                cycles = measure_ifg_cycles(tx, pads, gap_cycles=tx.gap_cycles, speed=speed)
                self.assertGreaterEqual(cycles*bits_per_10_cycles, 10*self.ifg_bits)


if __name__ == "__main__":
    unittest.main()
