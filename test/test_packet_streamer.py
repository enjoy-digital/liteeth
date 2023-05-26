'''
TODO add checking of output stream
'''
import unittest

from migen import *
from litex.soc.interconnect.stream_sim import *
from liteeth.common import *


class DUT(Module):
    def __init__(self, dw=8):
        self.source = stream.Endpoint(eth_phy_description(dw))

        ###

        self.dw = dw
        self.submodules.phy_source = PacketStreamer(
            eth_phy_description(dw), dw=dw
        )
        self.comb += [
            self.phy_source.source.connect(self.source)
        ]


def main_generator(dut):
    print()
    p = Packet(range(10))
    dut.phy_source.send(p)
    # dut.phy_source.send(p)
    for i in range(64):
        yield (dut.source.ready.eq(i % 2))
        yield


class TestPacketStreamer(unittest.TestCase):
    def test(self):
        dut = DUT(64)
        generators = {
            "sys": [
                main_generator(dut),
                dut.phy_source.generator()
            ],
        }
        run_simulation(dut, generators, vcd_name="sim.vcd")
