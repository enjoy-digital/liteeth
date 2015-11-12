from migen import *

from litex.soc.interconnect import wishbone

from liteeth.common import *
from liteeth.core.mac.core import LiteEthMACCore

from test.common import *
from test.model import phy, mac


class TB(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=True)
        self.submodules.core = LiteEthMACCore(phy=self.phy_model, dw=8, with_preamble_crc=True)

        self.submodules.streamer = PacketStreamer(eth_phy_description(8), last_be=1)
        self.submodules.streamer_randomizer = AckRandomizer(eth_phy_description(8), level=50)

        self.submodules.logger_randomizer = AckRandomizer(eth_phy_description(8), level=50)
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

    # XXX: find a way to exit properly
    import sys
    sys.exit()

if __name__ == "__main__":
    tb = TB()
    generators = {
        "sys" :   [main_generator(tb),
                   tb.streamer.generator(),
                   tb.streamer_randomizer.generator(),
                   tb.logger_randomizer.generator(),
                   tb.logger.generator()],
        "eth_tx": [tb.phy_model.phy_sink.generator(),
                   tb.phy_model.generator()],
        "eth_rx":  tb.phy_model.phy_source.generator()
    }
    clocks = {"sys":    10,
              "eth_rx": 10,
              "eth_tx": 10}
    run_simulation(tb, generators, clocks, vcd_name="sim.vcd")
