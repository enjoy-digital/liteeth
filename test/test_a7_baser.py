#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
from math import ceil

from migen import Cat, Constant, If, Instance, Record, Signal
from migen.sim import run_simulation

from liteeth.phy.serial.gtp_7series import QPLLChannel
from liteeth.phy.serial.baser.pma.gtp_7series import PMA_A7_GTP_5G_BASER

# Simulation ---------------------------------------------------------------------------------------

class GTPTestbench:
    """Exercise real PMA fabric logic; drive vendor outputs instead of modelling the analog GT/MMCMs."""
    def __init__(self):
        self.qpll = QPLLChannel(0)
        pads = Record([("rxp", 1), ("rxn", 1), ("txp", 1), ("txn", 1)])
        # Shorten the init timers, while retaining the production reset FSMs and synchronizers.
        self.dut = dut = PMA_A7_GTP_5G_BASER(self.qpll, pads, 10e6, 103.125e6)
        self.block = block = Signal(16)
        dut.comb += [
            dut.tx_data.eq(Cat(block ^ Constant(0x76543210, 32), block ^ Constant(0xfedcba98, 32))),
            dut.tx_header.eq(2),
        ]
        dut.sync.eth_tx += If(dut.tx_ce, block.eq(block + 1))
        self.fragment = dut.get_fragment()
        channel = next(s for s in self.fragment.specials
            if isinstance(s, Instance) and s.of == "GTPE2_CHANNEL")
        self.ports = {item.name: item.expr for item in channel.items
            if isinstance(item, (Instance.Input, Instance.Output))}
        instances = {s for s in self.fragment.specials if isinstance(s, Instance)}
        # FDCE samples the reset entering each MMCM; MMCM lock is driven by the test below.
        assert {s.of for s in instances} == {"GTPE2_CHANNEL", "MMCME2_ADV", "BUFG", "FDCE"}
        self.fragment.specials -= instances

    def run(self, generator, domain):
        run_simulation(self.fragment, {domain: generator},
            clocks={"sys": 10, "eth_tx": 8, "eth_rx": 12, "eth_tx_usr": 4, "eth_rx_usr": 6})

    def start_tx(self):
        yield self.qpll.lock.eq(1)
        for _ in range(1000):
            if (yield self.dut.tx_init.done):
                break
            yield
        else:
            raise AssertionError("TX initialization did not finish")
        yield self.dut.tx_cm.locked.eq(1)
        # Clock/reset release alone must not advance the gearbox before TXRESETDONE.
        for _ in range(8):
            yield
            assert (yield self.dut.tx_sequence) == 0
            assert (yield self.dut.tx_ce) == 0
        yield self.dut.tx_reset_done.eq(1)

    def tx_periods(self, periods=2):
        # Skip acquisition and begin at a full 33-cycle sequence.
        for _ in range(100):
            if (yield self.dut.tx_sequence) == 32:
                break
            yield
        else:
            raise AssertionError("TX gearbox did not start")
        yield
        first_block = (yield self.block)
        samples = []
        for _ in range(33*periods):
            samples.append(((yield self.dut.tx_sequence), (yield self.dut.tx_ce),
                (yield self.ports["TXDATA"]), (yield self.ports["TXHEADER"])))
            yield
        return first_block, samples

# Gearbox ------------------------------------------------------------------------------------------

class TestA7BASER(unittest.TestCase):
    def test_clocking_and_gtp_configuration(self):
        for refclk_freq in [103.125e6, 128.90625e6, 161.1328125e6, 171.875e6]:
            for channel in [0, 1]:
                with self.subTest(refclk_freq=refclk_freq, channel=channel):
                    pads = Record([("rxp", 1), ("rxn", 1), ("txp", 1), ("txn", 1)])
                    dut = PMA_A7_GTP_5G_BASER(QPLLChannel(channel), pads, 100e6, refclk_freq)
                    fragment = dut.get_fragment()
                    gt = next(s for s in fragment.specials
                        if isinstance(s, Instance) and s.of == "GTPE2_CHANNEL")
                    params = {item.name: item.value for item in gt.items
                        if isinstance(item, Instance.Parameter)}
                    ports = {item.name: item.expr for item in gt.items
                        if isinstance(item, Instance.Input)}
                    for direction in ["TX", "RX"]:
                        self.assertEqual(params[direction + "_DATA_WIDTH"].value, 32)
                        self.assertEqual(params[direction + "GEARBOX_EN"], "TRUE")
                        self.assertEqual(params[direction + "OUT_DIV"].value, 1)
                        self.assertEqual(params[direction + "_CLK25_DIV"].value, ceil(refclk_freq/25e6))
                        self.assertEqual(ports[direction + "SYSCLKSEL"].value, 0 if channel == 0 else 3)
                        self.assertEqual(ports[direction + "USRCLK"].cd, "eth_" + direction.lower() + "_usr")
                        self.assertEqual(ports[direction + "USRCLK2"].cd, "eth_" + direction.lower())
                    self.assertEqual(params["GEARBOX_MODE"].value, 1)
                    for mmcm in [dut.tx_cm, dut.rx_cm]:
                        mmcm_params = mmcm.params
                        vco = mmcm.clkin_freq*mmcm_params["p_CLKFBOUT_MULT_F"]/mmcm_params["p_DIVCLK_DIVIDE"]
                        self.assertAlmostEqual(vco/mmcm_params["p_CLKOUT0_DIVIDE_F"], 322.265625e6)
                        self.assertAlmostEqual(vco/mmcm_params["p_CLKOUT1_DIVIDE"], 161.1328125e6)

    def check_tx(self, first_block, samples):
        periods = len(samples)//33
        self.assertEqual([s[0] for s in samples], list(range(33))*periods)
        # UG482 Table 3-10: 16 blocks per 33 clocks, pause at 31, last lower word at 32.
        self.assertEqual([s[1] for s in samples], ([0, 1]*15 + [0, 0, 1])*periods)
        expected = []
        for block in range(first_block, first_block + 16*periods):
            expected.extend([block ^ 0xfedcba98, block ^ 0x76543210])
        self.assertEqual([data for seq, ce, data, hdr in samples if seq != 31], expected)
        self.assertTrue(all(hdr == 2 for seq, ce, data, hdr in samples))

    def test_tx_startup_cadence_and_word_order(self):
        tb = GTPTestbench()

        def generator():
            yield from tb.start_tx()
            first, samples = yield from tb.tx_periods(3)
            self.check_tx(first, samples)

        tb.run(generator(), "eth_tx")

    def test_tx_reset_recovery(self):
        tb = GTPTestbench()

        def generator():
            yield from tb.start_tx()
            first, samples = yield from tb.tx_periods()
            self.check_tx(first, samples)
            # Interrupt a block, then model reset-done and MMCM-lock dropping with channel reset.
            for _ in range(33):
                if (yield tb.dut.tx_sequence) == 30:
                    break
                yield
            else:
                self.fail("TX sequence did not reach the reset test point")
            yield tb.dut.reset.eq(1)
            for _ in range(4):
                yield
            self.assertEqual((yield tb.ports["GTTXRESET"]), 1)
            yield tb.dut.tx_reset_done.eq(0)
            yield tb.dut.tx_cm.locked.eq(0)
            for _ in range(8):
                yield
            self.assertEqual((yield tb.dut.tx_sequence), 0)
            self.assertEqual((yield tb.dut.tx_ce), 0)
            self.assertEqual((yield tb.block), 0)
            yield tb.dut.reset.eq(0)
            yield from tb.start_tx()
            first, samples = yield from tb.tx_periods()
            self.check_tx(first, samples)

        tb.run(generator(), "eth_tx")

    def test_tx_reset_done_loss(self):
        tb = GTPTestbench()

        def generator():
            yield from tb.start_tx()
            yield from tb.tx_periods(1)
            yield tb.dut.tx_reset_done.eq(0)
            for _ in range(8):
                yield
            self.assertEqual((yield tb.dut.tx_sequence), 0)
            self.assertEqual((yield tb.dut.tx_ce), 0)
            yield tb.dut.tx_reset_done.eq(1)
            first, samples = yield from tb.tx_periods()
            self.check_tx(first, samples)

        tb.run(generator(), "eth_tx")

    def test_rx_pairing_gaps_and_reset(self):
        tb = GTPTestbench()

        def word(data, valid=1, header_valid=0, header=2):
            yield tb.ports["RXDATA"].eq(data)
            yield tb.ports["RXDATAVALID"].eq(valid)
            yield tb.ports["RXHEADERVALID"].eq(header_valid)
            yield tb.ports["RXHEADER"].eq(header)
            yield

        def generator():
            yield tb.dut.rx_cm.locked.eq(1)
            for _ in range(8):
                yield
            # An orphan lower word must not become a block.
            yield from word(0x11111111)
            self.assertEqual((yield tb.dut.rx_ce), 0)
            for header in [1, 2]:
                yield from word(0xfedcba98, header_valid=1, header=header)
                self.assertEqual((yield tb.dut.rx_ce), 0)
                for _ in range(3):
                    yield from word(0xdeadbeef, valid=0)
                    self.assertEqual((yield tb.dut.rx_ce), 0)
                yield from word(0x76543210)
                self.assertEqual((yield tb.dut.rx_ce), 1)
                self.assertEqual((yield tb.dut.rx_data), 0xfedcba9876543210)
                self.assertEqual((yield tb.dut.rx_header), header)
                yield from word(0x22222222)
                self.assertEqual((yield tb.dut.rx_ce), 0)

            # A replacement header reacquires pairing; invalid data never captures a header.
            yield from word(0xaaaaaaaa, header_valid=1, header=1)
            yield from word(0xbbbbbbbb, header_valid=1, header=2)
            yield from word(0xcccccccc, valid=0, header_valid=1, header=1)
            yield from word(0xdddddddd)
            self.assertEqual((yield tb.dut.rx_data), 0xbbbbbbbbdddddddd)
            self.assertEqual((yield tb.dut.rx_header), 2)
            self.assertEqual((yield tb.dut.rx_ce), 1)

            # Reset between halves must discard the saved upper word.
            yield from word(0x12345678, header_valid=1)
            yield tb.dut.rx_cm.locked.eq(0)
            yield from word(0, valid=0)
            for _ in range(8):
                yield
            yield tb.dut.rx_cm.locked.eq(1)
            for _ in range(8):
                yield
            yield from word(0x87654321)
            self.assertEqual((yield tb.dut.rx_ce), 0)
            yield from word(0x01020304, header_valid=1)
            yield from word(0x05060708)
            self.assertEqual((yield tb.dut.rx_ce), 1)
            self.assertEqual((yield tb.dut.rx_data), 0x0102030405060708)

        tb.run(generator(), "eth_rx")

    def test_bitslip_pulse_and_holdoff(self):
        tb = GTPTestbench()

        def generator():
            yield tb.dut.rx_cm.locked.eq(1)
            for _ in range(8):
                yield
            pulses = []
            for cycle in range(220):
                # A held request, then frequent edges, then an isolated request after recovery.
                request = cycle < 20 or (20 <= cycle < 150 and cycle % 2 == 0) or cycle == 215
                yield tb.dut.rx_slip.eq(request)
                yield
                if (yield tb.ports["RXGEARBOXSLIP"]):
                    pulses.append(cycle)
            self.assertGreaterEqual(len(pulses), 3)
            self.assertEqual(sum(cycle < 20 for cycle in pulses), 1)
            self.assertTrue(all(b - a >= 65 for a, b in zip(pulses, pulses[1:])))
            self.assertGreaterEqual(pulses[-1], 215)
            # Reset clears the active holdoff and the edge detector.
            yield tb.dut.rx_cm.locked.eq(0)
            yield tb.dut.rx_slip.eq(0)
            for _ in range(8):
                yield
            yield tb.dut.rx_cm.locked.eq(1)
            for _ in range(8):
                yield
            yield tb.dut.rx_slip.eq(1)
            recovered = []
            for _ in range(4):
                yield
                recovered.append((yield tb.ports["RXGEARBOXSLIP"]))
            self.assertEqual(sum(recovered), 1)

        tb.run(generator(), "eth_rx")
