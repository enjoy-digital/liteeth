#
# This file is part of LiteEth.
#
# Copyright (c) 2026 luanvt
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.mac.core import LiteEthMACCore

from test.model import phy, mac

from litex.gen.sim import *

VCD_NAME = "sim.vcd"

# dw=8 at 10 ns/cycle -> 80 Mbps wire rate.
# 10 Mbps shaping = 1/8 byte per cycle in Q16.16.
RATE_10MBPS = 8192
CLK_PERIOD_NS = 10
CSR_COMMIT_DELAY_CYCLES = 32
N_PACKETS = 4
PAYLOAD_LENGTH = eth_mtu - mac_header.length - eth_fcs_length - eth_preamble_length


def expected_refill_cycles(charge_bytes, rate):
    return -(-(charge_bytes << 16) // rate)


def make_packet(seed, payload_length=PAYLOAD_LENGTH):
    packet = mac.MACPacket([((seed + i) & 0xff) for i in range(payload_length)])
    packet.target_mac    = 0x010203040506
    packet.sender_mac    = 0x090A0B0C0D0E
    packet.ethernet_type = 0x0800
    packet.encode_header()
    return packet


class TXMonitor:
    def __init__(self, endpoint, dw):
        self.endpoint = endpoint
        self.dw       = dw
        self.frames   = []
        self.starts   = []
        self.gaps     = []

    def _beat_bytes(self, last, last_be):
        if not last:
            return self.dw // 8
        for byte in range(self.dw // 8):
            if last_be == (1 << byte):
                return byte + 1
        return self.dw // 8

    @passive
    def generator(self):
        cycle          = 0
        current_frame  = []
        previous_cycle = None

        while True:
            if (yield self.endpoint.valid) and (yield self.endpoint.ready):
                data    = (yield self.endpoint.data)
                last    = (yield self.endpoint.last)
                last_be = (yield self.endpoint.last_be)

                if not current_frame:
                    self.starts.append(cycle)
                elif cycle != (previous_cycle + 1):
                    self.gaps.append((len(self.frames), previous_cycle, cycle))

                for byte in range(self._beat_bytes(last, last_be)):
                    current_frame.append((data >> (8*byte)) & 0xff)

                previous_cycle = cycle

                if last:
                    self.frames.append(current_frame)
                    current_frame  = []
                    previous_cycle = None

            cycle += 1
            yield


class DUT(LiteXModule):
    def __init__(self, logger_randomizer_level=0):
        self.phy_model = phy.PHY(8, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=True)
        self.core      = LiteEthMACCore(
            phy                  = self.phy_model,
            dw                   = 8,
            with_preamble_crc    = True,
            with_tx_rate_limiter = True,
            tx_rate_limiter_rate = RATE_10MBPS,
            tx_rate_limiter_burst = eth_mtu,
        )

        self.streamer = PacketStreamer(eth_phy_description(8), last_be=1)
        self.streamer_randomizer = Randomizer(eth_phy_description(8), level=0)

        self.logger_randomizer = Randomizer(eth_phy_description(8), level=logger_randomizer_level)
        self.logger = PacketLogger(eth_phy_description(8))

        self.tx_monitor = TXMonitor(self.phy_model.sink, 8)
        self.expected_packets = []
        self.received_packets = []

        self.comb += [
            Record.connect(self.streamer.source, self.streamer_randomizer.sink),
            Record.connect(self.streamer_randomizer.source, self.core.sink),
            Record.connect(self.core.source, self.logger_randomizer.sink),
            Record.connect(self.logger_randomizer.source, self.logger.sink)
        ]

        self.pipeline = stream.Pipeline(
            self.streamer,
            self.streamer_randomizer,
            self.core,
            self.logger_randomizer,
            self.logger,
        )


def control_generator(dut):
    yield dut.core.tx_rate_limiter._rate.storage.eq(RATE_10MBPS)
    yield dut.core.tx_rate_limiter._burst.storage.eq(eth_mtu)
    for _ in range(CSR_COMMIT_DELAY_CYCLES):
        yield
    yield dut.core.tx_rate_limiter._enable.storage.eq(1)


def main_generator(dut):
    for _ in range(CSR_COMMIT_DELAY_CYCLES + 8):
        yield

    packets = [make_packet(seed=i*17) for i in range(N_PACKETS)]
    dut.expected_packets = packets

    for packet in packets:
        dut.streamer.send(packet)

    for _ in packets:
        yield from dut.logger.receive()
        dut.received_packets.append(dut.logger.packet)


class TestMACCoreWithRateLimiter(unittest.TestCase):
    def test_tx_rate_limiter_integration(self):
        dut = DUT()
        generators = {
            "sys": [
                main_generator(dut),
                control_generator(dut),
                dut.streamer.generator(),
                dut.streamer_randomizer.generator(),
                dut.logger_randomizer.generator(),
                dut.logger.generator(),
            ],
            "eth_tx": [
                dut.phy_model.phy_sink.generator(),
                dut.phy_model.generator(),
                dut.tx_monitor.generator(),
            ],
            "eth_rx": [
                dut.phy_model.phy_source.generator(),
            ]
        }
        clocks = {
            "sys":    CLK_PERIOD_NS,
            "eth_rx": CLK_PERIOD_NS,
            "eth_tx": CLK_PERIOD_NS,
        }
        run_simulation(dut, generators, clocks, vcd_name=VCD_NAME)

        self.assertEqual(len(dut.received_packets), N_PACKETS)
        for expected, received in zip(dut.expected_packets, dut.received_packets):
            s, l, e = check(expected, received)
            self.assertEqual(e, 0)

        self.assertEqual(len(dut.tx_monitor.frames), N_PACKETS)
        self.assertEqual(dut.tx_monitor.gaps, [])
        for frame in dut.tx_monitor.frames:
            self.assertEqual(len(frame), eth_mtu)

        refill = expected_refill_cycles(eth_mtu, RATE_10MBPS)
        expected_spacing = eth_mtu + refill
        spacings = [dut.tx_monitor.starts[i+1] - dut.tx_monitor.starts[i]
            for i in range(N_PACKETS - 1)]
        for spacing in spacings:
            self.assertAlmostEqual(spacing, expected_spacing, delta=1)

    def test_tx_rate_limiter_integration_with_random_logger_ready(self):
        random.seed(2026)
        dut = DUT(logger_randomizer_level=50)
        generators = {
            "sys": [
                main_generator(dut),
                control_generator(dut),
                dut.streamer.generator(),
                dut.streamer_randomizer.generator(),
                dut.logger_randomizer.generator(),
                dut.logger.generator(),
            ],
            "eth_tx": [
                dut.phy_model.phy_sink.generator(),
                dut.phy_model.generator(),
                dut.tx_monitor.generator(),
            ],
            "eth_rx": [
                dut.phy_model.phy_source.generator(),
            ]
        }
        clocks = {
            "sys":    CLK_PERIOD_NS,
            "eth_rx": CLK_PERIOD_NS,
            "eth_tx": CLK_PERIOD_NS,
        }
        run_simulation(dut, generators, clocks, vcd_name="sim_random_logger_ready.vcd")

        self.assertEqual(len(dut.received_packets), N_PACKETS)
        for expected, received in zip(dut.expected_packets, dut.received_packets):
            s, l, e = check(expected, received)
            self.assertEqual(e, 0)

        self.assertEqual(len(dut.tx_monitor.frames), N_PACKETS)
        self.assertEqual(dut.tx_monitor.gaps, [])
        for frame in dut.tx_monitor.frames:
            self.assertEqual(len(frame), eth_mtu)

        refill = expected_refill_cycles(eth_mtu, RATE_10MBPS)
        expected_spacing = eth_mtu + refill
        spacings = [dut.tx_monitor.starts[i+1] - dut.tx_monitor.starts[i]
            for i in range(N_PACKETS - 1)]
        for spacing in spacings:
            self.assertAlmostEqual(spacing, expected_spacing, delta=1)
