#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.mac.core import LiteEthMACCore

from test.model import phy, mac

from litex.gen.sim import *


class DUT(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=True)
        self.submodules.core = LiteEthMACCore(phy=self.phy_model, dw=8, with_preamble_crc=True)

        self.submodules.streamer = PacketStreamer(eth_phy_description(8), last_be=1)
        self.submodules.streamer_randomizer = Randomizer(eth_phy_description(8), level=50)

        self.submodules.logger_randomizer = Randomizer(eth_phy_description(8), level=50)
        self.submodules.logger = PacketLogger(eth_phy_description(8))

        self.comb += [
            Record.connect(self.streamer.source, self.streamer_randomizer.sink),
            Record.connect(self.streamer_randomizer.source, self.core.sink),
            Record.connect(self.core.source, self.logger_randomizer.sink),
            Record.connect(self.logger_randomizer.source, self.logger.sink)
        ]


def main_generator(dut):
    for i in range(2):
        packet = mac.MACPacket([i for i in range(64)])
        packet.target_mac = 0x010203040506
        packet.sender_mac = 0x090A0B0C0C0D
        packet.ethernet_type = 0x0800
        packet.encode_header()
        dut.streamer.send(packet)
        yield from dut.logger.receive()

        # check results
        s, l, e = check(packet, dut.logger.packet)
        print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))

class TestMACCore(unittest.TestCase):
    def test(self):
        dut = DUT()
        generators = {
            "sys" :   [main_generator(dut),
                       dut.streamer.generator(),
                       dut.streamer_randomizer.generator(),
                       dut.logger_randomizer.generator(),
                       dut.logger.generator()],
            "eth_tx": [dut.phy_model.phy_sink.generator(),
                       dut.phy_model.generator()],
            "eth_rx":  dut.phy_model.phy_source.generator()
        }
        clocks = {"sys":    10,
                  "eth_rx": 10,
                  "eth_tx": 10}
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")
