from litex.gen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.core import LiteEthIPCore

from test.model import phy, mac, arp, ip

ip_address = 0x12345678
mac_address = 0x12345678abcd


class TB(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=True)

        self.submodules.ip = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000)
        self.ip_port = self.ip.ip.crossbar.get_port(udp_protocol)

def main_generator(dut):
    while True:
        yield dut.ip_port.sink.valid.eq(1)
        yield dut.ip_port.sink.last.eq(1)
        yield dut.ip_port.sink.ip_address.eq(0x12345678)
        yield dut.ip_port.sink.protocol.eq(udp_protocol)

        yield dut.ip_port.source.ready.eq(1)
        if (yield dut.ip_port.source.valid) == 1 and (yield dut.ip_port.source.last) == 1:
            print("packet from IP 0x{:08x}".format((yield dut.ip_port.sink.ip_address)))
            # XXX: find a way to exit properly
            import sys
            sys.exit()

        yield

if __name__ == "__main__":
    tb = TB()
    generators = {
        "sys" :   [main_generator(tb)],
        "eth_tx": [tb.phy_model.phy_sink.generator(),
                   tb.phy_model.generator()],
        "eth_rx":  tb.phy_model.phy_source.generator()
    }
    clocks = {"sys":    10,
              "eth_rx": 10,
              "eth_tx": 10}
    run_simulation(tb, generators, clocks, vcd_name="sim.vcd")
