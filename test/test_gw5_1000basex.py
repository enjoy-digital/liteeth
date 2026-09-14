#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import hashlib
import unittest
from types import SimpleNamespace

from migen import *
from migen.sim import passive

from liteeth.phy.gw5_1000basex import GW5_1000BASEX


class SerDesLoopback:
    def __init__(self, lane):
        self.lane = lane

    def lower(self, instance):
        lane = self.lane
        assert instance.of == "GTR12_QUAD"
        model = Module()
        model.comb += [
            instance.get_io(f"FABRIC_LN{lane}_RXDATA_O").eq(instance.get_io(f"FABRIC_LN{lane}_TXDATA_I")),
            instance.get_io(f"FABRIC_LN{lane}_RX_VLD_OUT").eq(1),
            instance.get_io(f"LANE{lane}_RX_IF_FIFO_EMPTY").eq(0),
            instance.get_io(f"FABRIC_LANE{lane}_CMU_OK_O").eq(1),
            instance.get_io(f"FABRIC_LN{lane}_PMA_RX_LOCK_O").eq(1),
            instance.get_io(f"LANE{lane}_ALIGN_LINK").eq(1),
        ]
        return model


class TestGW51000BASEX(unittest.TestCase):
    def test_registered_datapath_autonegotiates_and_transfers_packets_after_reset(self):
        for lane in (0, 1):
            with self.subTest(lane=lane):
                self._test_loopback(lane)

    def test_serdes_configuration_matches_vendor_register_writes(self):
        # SHA256 of Gowin 1.9.12's ordered register writes, excluding comments.
        expected = {
            0 : "850bf0dc8b02d146e5aadfb9184622e8aa9f973b8ae9efcb2b7684e9b235d184",
            1 : "3645f8fcfbe2ff2db12e1768aea871ef5d97b00b35b276ec1487cac48382ce05",
        }
        for lane, checksum in expected.items():
            with self.subTest(lane=lane):
                platform = SimpleNamespace(devicename="GW5AST-138B",
                    toolchain=SimpleNamespace(additional_tcl_commands=[]))
                GW5_1000BASEX(platform, lane=lane, with_csr=False)
                commands = "\n".join(platform.toolchain.additional_tcl_commands)
                writes = "\n".join(line.split("#")[0].strip()
                    for line in commands.splitlines() if line.startswith("upar_write_driver")) + "\n"
                self.assertEqual(hashlib.sha256(writes.encode()).hexdigest(), checksum)

    def _test_loopback(self, lane):
        platform = SimpleNamespace(devicename="GW5AST-138B",
            toolchain=SimpleNamespace(additional_tcl_commands=[]))
        dut = GW5_1000BASEX(platform, with_csr=False, lane=lane, pcs_kwargs={
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
            special_overrides={Instance: SerDesLoopback(lane)})


if __name__ == "__main__":
    unittest.main()
