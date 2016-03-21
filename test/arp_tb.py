from litex.gen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.core.mac import LiteEthMAC
from liteeth.core.arp import LiteEthARP

from test.model import phy, mac, arp

ip_address = 0x12345678
mac_address = 0x12345678abcd


class TB(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)

        self.submodules.mac = LiteEthMAC(self.phy_model, dw=8, with_preamble_crc=True)
        self.submodules.arp = LiteEthARP(self.mac, mac_address, ip_address, 100000)


def main_generator(dut):
    while (yield dut.arp.table.request.ready) != 1:
        yield dut.arp.table.request.valid.eq(1)
        yield dut.arp.table.request.ip_address.eq(0x12345678)
        yield
    yield dut.arp.table.request.valid.eq(0)
    while (yield dut.arp.table.response.valid) != 1:
        yield dut.arp.table.response.ready.eq(1)
        yield
    print("Received MAC : 0x{:12x}".format((yield dut.arp.table.response.mac_address)))

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
