from litex.gen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.core import LiteEthIPCore

from model.dumps import *
from model.mac import *
from model.ip import *
from model.icmp import *
from model import phy, mac, arp, ip, icmp

ip_address = 0x12345678
mac_address = 0x12345678abcd


class TB(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=True)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=True, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=True)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=True, loopback=False)
        self.submodules.icmp_model = icmp.ICMP(self.ip_model, ip_address, debug=True)

        self.submodules.ip = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000)

def main_generator(dut):
    packet = MACPacket(ping_request)
    packet.decode_remove_header()
    packet = IPPacket(packet)
    packet.decode()
    packet = ICMPPacket(packet)
    packet.decode()
    dut.icmp_model.send(packet)

    for i in range(256):
        yield

    # XXX: find a way to exit properly
    import sys
    sys.exit()

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
