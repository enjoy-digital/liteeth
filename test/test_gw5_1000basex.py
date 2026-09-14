#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from types import SimpleNamespace

from migen import *
from migen.sim import passive

from liteeth.phy.gw5_1000basex import GW5_1000BASEX


class SerDesLoopback:
    @staticmethod
    def lower(instance):
        assert instance.of == "GTR12_QUAD"
        model = Module()
        model.comb += [
            instance.get_io("FABRIC_LN0_RXDATA_O").eq(instance.get_io("FABRIC_LN0_TXDATA_I")),
            instance.get_io("FABRIC_LN0_RX_VLD_OUT").eq(1),
            instance.get_io("LANE0_RX_IF_FIFO_EMPTY").eq(0),
            instance.get_io("FABRIC_LANE0_CMU_OK_O").eq(1),
            instance.get_io("FABRIC_LN0_PMA_RX_LOCK_O").eq(1),
            instance.get_io("LANE0_ALIGN_LINK").eq(1),
        ]
        return model


class TestGW51000BASEX(unittest.TestCase):
    def test_registered_datapath_autonegotiates_and_transfers_packets_after_reset(self):
        platform = SimpleNamespace(devicename="GW5AST-138B",
            toolchain=SimpleNamespace(additional_tcl_commands=[]))
        dut = GW5_1000BASEX(platform, with_csr=False, pcs_kwargs={
            "check_period": 64/125e6,
            "breaklink_time": 16/125e6,
            "more_ack_time": 16/125e6,
        })
        dut.cd_sys = ClockDomain("sys")
        packets = []

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
            for trial in range(2):
                yield dut.reset.eq(1)
                for _ in range(8):
                    yield
                yield dut.reset.eq(0)
                for _ in range(1024):
                    if (yield dut.link_up):
                        break
                    yield
                else:
                    self.fail("PCS did not establish a link")

                packet = [0x55]*7 + [0xd5] + [(i + trial) % 256 for i in range(64)]
                for i, byte in enumerate(packet):
                    yield dut.sink.valid.eq(1)
                    yield dut.sink.data.eq(byte)
                    yield dut.sink.last.eq(i == len(packet) - 1)
                    yield
                    while not (yield dut.sink.ready):
                        yield
                yield dut.sink.valid.eq(0)
                for _ in range(32):
                    yield
                self.assertEqual(packets[trial], packet)

        run_simulation(dut, {"eth_tx": sender(), "eth_rx": receiver()},
            clocks={"sys": 20, "eth_tx": 8, "eth_rx": 8},
            special_overrides={Instance: SerDesLoopback})


if __name__ == "__main__":
    unittest.main()
