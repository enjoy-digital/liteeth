#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import hashlib
import random
import unittest
from types import SimpleNamespace

from migen import *
from migen.sim import passive

from liteeth.phy.serial.baser.pma.gw5 import GW5BASERTXGearbox, GW5BASERRXGearbox
from liteeth.phy.serial.baser.wrappers.gw5 import GW5_10G_BASER
from liteeth.phy.serial.baser.pma.gw5_config import _serdes_csr_generated, _cdr_vco_bias


def gw5_platform():
    return SimpleNamespace(devicename="GW5AST-138B",
        toolchain=SimpleNamespace(additional_tcl_commands=[]))


class SerDesLoopback:
    def __init__(self, lane, delay=0):
        self.lane  = lane
        self.delay = delay

    def lower(self, instance):
        lane = self.lane
        assert instance.of == "GTR12_QUAD"
        model = Module()
        # Delay the serial stream by `delay` bits so the PCS must find the block boundary.
        tx_data = instance.get_io(f"FABRIC_LN{lane}_TXDATA_I")
        words   = Signal(128)
        model.sync.eth_tx += words.eq(Cat(words[64:], *[tx_data[10*n:10*n + 8] for n in range(8)]))
        rx_word = words[64 - self.delay:128 - self.delay]
        model.comb += [
            instance.get_io(f"FABRIC_LN{lane}_RXDATA_O").eq(
                Cat(*[Cat(rx_word[8*n:8*(n + 1)], Constant(0, 2)) for n in range(8)])),
            instance.get_io(f"FABRIC_LN{lane}_RX_VLD_OUT").eq(1),
            instance.get_io(f"LANE{lane}_RX_IF_FIFO_EMPTY").eq(0),
            instance.get_io(f"FABRIC_LANE{lane}_CMU_OK_O").eq(1),
            instance.get_io(f"FABRIC_LN{lane}_PMA_RX_LOCK_O").eq(1),
        ]
        return model


class GearboxLoopback(Module):
    def __init__(self, delay):
        self.submodules.tx = GW5BASERTXGearbox()
        self.submodules.rx = GW5BASERRXGearbox()
        # Offset the serial stream by `delay` bits to emulate an unknown block boundary.
        stream = Signal(128)
        self.sync += stream.eq(Cat(stream[64:], self.tx.output))
        self.comb += self.rx.input.eq(stream[64 - delay:128 - delay])


class TestGW5BASERGearbox(unittest.TestCase):
    def test_tx_cadence_and_rx_alignment(self):
        for delay in (0, 1, 33, 63):
            with self.subTest(delay=delay):
                self._test_loopback(delay)

    def _test_loopback(self, delay):
        rng   = random.Random(delay)
        dut   = GearboxLoopback(delay)
        sent  = []
        state = {"aligned": None, "received": []}

        def tx_generator():
            ce_history = []
            yield dut.tx.data.eq(rng.getrandbits(64))
            yield dut.tx.header.eq(0b01)
            yield
            for cycle in range(33*20):
                if (yield dut.tx.ce):
                    sent.append(((yield dut.tx.data), (yield dut.tx.header)))
                    yield dut.tx.data.eq(rng.getrandbits(64))
                    yield dut.tx.header.eq(rng.choice([0b01, 0b10]))
                ce_history.append((yield dut.tx.ce))
                yield
            # 32 blocks every 33 cycles.
            self.assertEqual(sum(ce_history[:33*16]), 32*16)

        def rx_generator():
            slip_wait = 0
            for cycle in range(33*20):
                yield dut.rx.slip.eq(0)
                if (yield dut.rx.ce):
                    block = ((yield dut.rx.data), (yield dut.rx.header))
                    if state["aligned"] is None:
                        # Slip until the received block matches a transmitted block.
                        if block in sent:
                            state["aligned"] = sent.index(block)
                        elif slip_wait == 0:
                            yield dut.rx.slip.eq(1)
                            slip_wait = 4
                    if state["aligned"] is not None:
                        state["received"].append(block)
                    slip_wait = max(slip_wait - 1, 0)
                yield

        run_simulation(dut, [tx_generator(), rx_generator()])
        self.assertIsNotNone(state["aligned"])
        start    = state["aligned"]
        received = state["received"]
        self.assertGreater(len(received), 200)
        self.assertEqual(received, sent[start:start + len(received)])



class TestGW510GBASER(unittest.TestCase):
    def test_serdes_configuration_matches_vendor_register_writes(self):
        # SHA256 of Gowin 1.9.12.04's ordered register writes, excluding comments.
        expected = {
            0 : "28aeb28da4e5a7080437efe320bc0085dc19a0840960fa52895b5838d5fd4bf4",
            1 : "00a99896fd2f92f061c21ff3de55f0459e4754f9bd07025f485bdfa18f63c8c4",
        }
        def writes(csr):
            return [line.split("#")[0].strip() for line in csr.splitlines()
                if line.startswith("upar_write_driver")]
        for lane, checksum in expected.items():
            with self.subTest(lane=lane):
                generated = "\n".join(writes(_serdes_csr_generated[lane])) + "\n"
                self.assertEqual(hashlib.sha256(generated.encode()).hexdigest(), checksum)

                # The build loads the generated writes, with only the CDR VCO bias overridden.
                platform = gw5_platform()
                GW5_10G_BASER(platform, lane=lane, with_csr=False)
                commands = "\n".join(platform.toolchain.additional_tcl_commands)
                self.assertIn("set_csr gw5_10gbaser.csr", commands)
                address, _, override = _cdr_vco_bias[lane]
                loaded = writes(commands)
                diff   = [(a, b) for a, b in zip(writes(_serdes_csr_generated[lane]), loaded) if a != b]
                self.assertEqual(len(loaded), len(writes(_serdes_csr_generated[lane])))
                self.assertEqual([b for a, b in diff],
                    [f"upar_write_driver(0x{address:06x},0x{override:08X})"])

    def test_loopback_block_lock_and_packets(self):
        for lane, delay in ((0, 0), (1, 37)):
            with self.subTest(lane=lane, delay=delay):
                self._test_loopback(lane, delay)

    def _test_loopback(self, lane, delay):
        dut = GW5_10G_BASER(gw5_platform(), lane=lane, with_csr=False, with_prbs=False)
        dut.cd_sys = ClockDomain("sys")
        packets = []

        @passive
        def receiver():
            packet = []
            yield dut.source.ready.eq(1)
            while True:
                if (yield dut.source.valid):
                    self.assertEqual((yield dut.source.error), 0)
                    data = (yield dut.source.data)
                    be     = (yield dut.source.be)
                    packet += [(data >> 8*n) & 0xff for n in range(8) if be & (1 << n)]
                    if (yield dut.source.last):
                        packets.append(packet)
                        packet = []
                yield

        def sender():
            for _ in range(8000):
                if (yield dut.pcs.rx_block_lock):
                    break
                yield
            else:
                self.fail("PCS did not acquire block lock")
            for _ in range(64):
                yield
            for trial in range(2):
                packet = [0x55]*7 + [0xd5] + [(i + trial) % 256 for i in range(60 + 3*trial)]
                words  = [packet[i:i + 8] for i in range(0, len(packet), 8)]
                for i, word in enumerate(words):
                    yield dut.sink.valid.eq(1)
                    yield dut.sink.data.eq(sum(b << 8*n for n, b in enumerate(word)))
                    yield dut.sink.last.eq(i == len(words) - 1)
                    yield dut.sink.be.eq((1 << len(word)) - 1)
                    yield
                    while not (yield dut.sink.ready):
                        yield
                yield dut.sink.valid.eq(0)
                for _ in range(64):
                    yield
                self.assertEqual(packets[trial], packet)

        run_simulation(dut, {"eth_tx": sender(), "eth_rx": receiver()},
            clocks={"sys": 20, "eth_tx": 8, "eth_rx": 8},
            special_overrides={Instance: SerDesLoopback(lane, delay)})


if __name__ == "__main__":
    unittest.main()
