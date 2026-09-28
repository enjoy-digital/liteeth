#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace
from unittest import mock

from migen import *
from migen.sim import passive

from litex.gen import *

from liteeth.phy.serial.basex.pcs import PCS
from liteeth.phy.serial.basex.wrappers import k7_gtx


class LoopbackPMA(LiteXModule):
    """Ideal raw-symbol adapter; real gearbox ordering has separate PCS tests."""
    def __init__(self, *args, **kwargs):
        self.cd_eth_tx      = ClockDomain()
        self.cd_eth_rx      = ClockDomain()
        self.cd_eth_tx_half = ClockDomain(reset_less=True)
        self.cd_eth_rx_half = ClockDomain(reset_less=True)
        self.reset    = Signal()
        self.align    = Signal()
        self.restart  = Signal()
        self.rx_valid = Signal(reset=1)
        self.txoutclk = Signal()
        self.rxoutclk = Signal()
        for name in ("pll", "tx_mmcm", "rx_mmcm", "tx_init", "rx_init", "gearbox"):
            setattr(self, name, SimpleNamespace())
        self.tx_data = Signal(10)
        self.rx_data = Signal(10)
        self.sync.eth_rx += self.rx_data.eq(self.tx_data)
        self.comb += [
            self.cd_eth_tx.rst.eq(self.reset),
            self.cd_eth_rx.rst.eq(self.reset),
        ]


class TestBASEXBoundary(unittest.TestCase):
    def test_autonegotiation_and_packets_through_pma_symbols(self):
        def make_pcs(**kwargs):
            return PCS(check_period=64/125e6, breaklink_time=16/125e6,
                more_ack_time=16/125e6, **kwargs)

        with mock.patch.object(k7_gtx, "PMA_K7_GTX_BASEX", LoopbackPMA), \
             mock.patch.object(k7_gtx, "PCS", side_effect=make_pcs):
            dut = k7_gtx.K7_1000BASEX(Signal(), None, 100e6, with_csr=False)

        packets = []
        restarts = []

        @passive
        def receiver():
            packet = []
            yield dut.source.ready.eq(1)
            while True:
                if (yield dut.source.valid):
                    self.assertEqual((yield dut.source.error), 0)
                    packet.append((yield dut.source.data))
                    if (yield dut.source.last):
                        packets.append(packet)
                        packet = []
                yield

        def sender():
            # With no valid RX symbols, the PCS must request recovery through the PMA boundary.
            yield dut.pma.rx_valid.eq(0)
            for _ in range(512):
                restarts.append((yield dut.pma.restart))
                self.assertEqual((yield dut.link_up), 0)
                yield
            self.assertIn(1, restarts)
            yield dut.pma.rx_valid.eq(1)

            for trial in range(2):
                if trial:
                    yield dut.reset.eq(1)
                    for _ in range(16):
                        yield
                    yield dut.reset.eq(0)
                for _ in range(2048):
                    if (yield dut.link_up):
                        break
                    yield
                else:
                    self.fail("PCS did not establish a link through the PMA symbol interface")
                packet = [0x55]*7 + [0xd5] + [(i + trial) % 256 for i in range(64)]
                for index, byte in enumerate(packet):
                    yield dut.sink.valid.eq(1)
                    yield dut.sink.data.eq(byte)
                    yield dut.sink.last.eq(index == len(packet) - 1)
                    yield
                    while not (yield dut.sink.ready):
                        yield
                yield dut.sink.valid.eq(0)
                for _ in range(64):
                    yield
                self.assertEqual(packets[trial], packet)

        run_simulation(dut, {"eth_tx": sender(), "eth_rx": receiver()},
            clocks={"eth_tx": 8, "eth_rx": 8, "eth_tx_half": 16, "eth_rx_half": 16})


if __name__ == "__main__":
    unittest.main()
