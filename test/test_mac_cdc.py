#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *

from litex.soc.interconnect import stream

from liteeth.common import eth_phy_description
from liteeth.mac.core import LiteEthMACCDC, LiteEthMACCore


class CDCDUT(LiteXModule):
    def __init__(self, cd_from, cd_to):
        self.cd_sys = ClockDomain("sys")
        self.cd_eth = ClockDomain("eth")
        self.cdc    = LiteEthMACCDC([("data", 8)], cd_from=cd_from, cd_to=cd_to, depth=8, buffered=False)


class TestMACCDC(unittest.TestCase):
    def check_one_sided_reset(self, cd_from, cd_to, cd_reset):
        """Reset one side only and check no stale word leaks and new words go through."""
        dut      = CDCDUT(cd_from, cd_to)
        received = []
        state    = dict(writes_done=False, reset_done=False)

        def write(datas):
            for data in datas:
                yield dut.cdc.sink.valid.eq(1)
                yield dut.cdc.sink.data.eq(data)
                yield
                while not (yield dut.cdc.sink.ready):
                    yield
            yield dut.cdc.sink.valid.eq(0)

        def wait(cycles):
            for _ in range(cycles):
                yield

        def writer():
            yield from wait(8)
            yield from write([0x10, 0x11, 0x12, 0x13, 0x14])
            yield from wait(64)
            state["writes_done"] = True
            while not state["reset_done"]:
                yield
            yield from wait(64)
            yield from write([0x20, 0x21, 0x22])
            yield from wait(64)

        def resetter():
            while not state["writes_done"]:
                yield
            cd = getattr(dut, f"cd_{cd_reset}")
            yield cd.rst.eq(1)
            yield from wait(8)
            yield cd.rst.eq(0)
            state["reset_done"] = True
            yield from wait(256)

        def reader():
            yield "passive"
            yield dut.cdc.source.ready.eq(1)
            while True:
                if (yield dut.cdc.source.valid):
                    received.append((yield dut.cdc.source.data))
                yield

        generators = {
            cd_from : [writer()],
            cd_to   : [reader()],
        }
        generators.setdefault(cd_reset, []).append(resetter())
        run_simulation(dut, generators, clocks={"sys": 10, "eth": 8})

        self.assertEqual(received, [0x10, 0x11, 0x12, 0x13, 0x14, 0x20, 0x21, 0x22])

    def test_tx_sys_reset(self):
        # SoC reboot: sys (write side) reset, eth (read side) kept running.
        self.check_one_sided_reset(cd_from="sys", cd_to="eth", cd_reset="sys")

    def test_tx_eth_reset(self):
        # PHY restart: eth (read side) reset, sys (write side) kept running.
        self.check_one_sided_reset(cd_from="sys", cd_to="eth", cd_reset="eth")

    def test_rx_eth_reset(self):
        # PHY restart: eth (write side) reset, sys (read side) kept running.
        self.check_one_sided_reset(cd_from="eth", cd_to="sys", cd_reset="eth")

    def test_rx_sys_reset(self):
        # SoC reboot: sys (read side) reset, eth (write side) kept running.
        self.check_one_sided_reset(cd_from="eth", cd_to="sys", cd_reset="sys")


class StreamPHY(LiteXModule):
    dw                      = 8
    integrated_ifg_inserter = True
    def __init__(self):
        self.sink   = stream.Endpoint(eth_phy_description(8))
        self.source = stream.Endpoint(eth_phy_description(8))


class MACCoreDUT(LiteXModule):
    def __init__(self):
        self.cd_sys    = ClockDomain("sys")
        self.cd_eth_tx = ClockDomain("eth_tx")
        self.cd_eth_rx = ClockDomain("eth_rx")
        self.phy       = StreamPHY()
        self.core      = LiteEthMACCore(self.phy, dw=8,
            with_preamble_crc      = False,
            with_padding           = False,
            with_store_and_forward = True,
        )


class TestMACCoreReset(unittest.TestCase):
    def test_tx_sys_reset_mid_frame(self):
        # A frame interrupted by a SoC reset must not be left in the eth_tx packet FIFO, where it
        # would be merged with the next frame.
        dut    = MACCoreDUT()
        frames = []
        state  = dict(done=False)

        def write(datas, last=True):
            for i, data in enumerate(datas):
                yield dut.core.sink.valid.eq(1)
                yield dut.core.sink.data.eq(data)
                yield dut.core.sink.last.eq(int(last and (i == len(datas) - 1)))
                yield
                while not (yield dut.core.sink.ready):
                    yield
            yield dut.core.sink.valid.eq(0)

        def sys_generator():
            for _ in range(16):
                yield
            yield from write([0xa0, 0xa1, 0xa2, 0xa3, 0xa4], last=False)
            for _ in range(64):
                yield
            yield dut.cd_sys.rst.eq(1)
            for _ in range(8):
                yield
            yield dut.cd_sys.rst.eq(0)
            for _ in range(64):
                yield
            yield from write([0xb0, 0xb1, 0xb2, 0xb3])
            for _ in range(256):
                yield

        def phy_generator():
            yield "passive"
            yield dut.phy.sink.ready.eq(1)
            frame = []
            while True:
                if (yield dut.phy.sink.valid):
                    frame.append((yield dut.phy.sink.data))
                    if (yield dut.phy.sink.last):
                        frames.append(frame)
                        frame = []
                yield

        run_simulation(dut, {"sys": [sys_generator()], "eth_tx": [phy_generator()]},
            clocks={"sys": 10, "eth_tx": 8, "eth_rx": 8})

        self.assertEqual(frames, [[0xb0, 0xb1, 0xb2, 0xb3]])


if __name__ == "__main__":
    unittest.main()
