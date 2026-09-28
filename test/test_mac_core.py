#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from test.stream_helpers import *

from liteeth.common import *
from liteeth.mac.core import LiteEthMACCore

from test.model import phy, mac

from litex.gen.sim import *

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, with_store_and_forward=False, rx_clk_freq=None):
        self.phy_model = phy.PHY(8, debug=False)
        if rx_clk_freq is not None:
            self.phy_model.rx_clk_freq = rx_clk_freq
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=True)
        self.core      = LiteEthMACCore(phy=self.phy_model, dw=8, with_preamble_crc=True,
            with_store_and_forward=with_store_and_forward)

        self.streamer = PacketStreamer(eth_phy_description(8), last_be=1)
        self.streamer_randomizer = Randomizer(eth_phy_description(8), level=50)

        self.logger_randomizer = Randomizer(eth_phy_description(8), level=50)
        self.logger = PacketLogger(eth_phy_description(8))

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

# Generator ----------------------------------------------------------------------------------------

def main_generator(dut):
    for i in range(2):
        packet = mac.MACPacket([i for i in range(64)])
        packet.target_mac    = 0x010203040506
        packet.sender_mac    = 0x090A0B0C0C0D
        packet.ethernet_type = 0x0800
        packet.encode_header()
        dut.streamer.send(packet)
        yield from dut.logger.receive()

        # check results
        s, l, e = check(packet, dut.logger.packet)
        print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))

# Test MAC Core ------------------------------------------------------------------------------------

class TestMACCore(unittest.TestCase):
    def loopback_test(self, with_store_and_forward, rx_clk_freq=None):
        dut = DUT(with_store_and_forward=with_store_and_forward, rx_clk_freq=rx_clk_freq)
        generators = {
            "sys" :   [
                main_generator(dut),
                dut.streamer.generator(),
                dut.streamer_randomizer.generator(),
                dut.logger_randomizer.generator(),
                dut.logger.generator()
            ],
            "eth_tx": [
                dut.phy_model.phy_sink.generator(),
                dut.phy_model.generator()
            ],
            "eth_rx":  [
                dut.phy_model.phy_source.generator()
            ]
        }
        clocks = {
            "sys"    : 10,
            "eth_rx" : 10,
            "eth_tx" : 10,
        }
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")

    def test(self):
        self.loopback_test(with_store_and_forward=False)

    def test_store_and_forward(self):
        self.loopback_test(with_store_and_forward=True)

    def test_store_and_forward_pipelined(self):
        # A PHY clock above eth_pipelining_clk_freq adds the register stage ahead of the transmit
        # packet FIFO.
        self.loopback_test(with_store_and_forward=True, rx_clk_freq=390.625e6)
