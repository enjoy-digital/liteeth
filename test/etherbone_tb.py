from litex.gen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.core import LiteEthUDPIPCore
from liteeth.frontend.etherbone import LiteEthEtherbone

from test.model import phy, mac, arp, ip, udp, etherbone

ip_address = 0x12345678
mac_address = 0x12345678abcd


class TB(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=True)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=True, loopback=False)
        self.submodules.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=True)
        self.submodules.ip_model = ip.IP(self.mac_model, mac_address, ip_address, debug=True, loopback=False)
        self.submodules.udp_model = udp.UDP(self.ip_model, ip_address, debug=True, loopback=False)
        self.submodules.etherbone_model = etherbone.Etherbone(self.udp_model, debug=True)

        self.submodules.core = LiteEthUDPIPCore(self.phy_model, mac_address, ip_address, 100000)
        self.submodules.etherbone = LiteEthEtherbone(self.core.udp, 20000)

        self.submodules.sram = wishbone.SRAM(1024)
        self.submodules.interconnect = wishbone.InterconnectPointToPoint(self.etherbone.master.bus, self.sram.bus)


def main_generator(dut):
    test_probe = True
    test_writes = False
    test_reads = False

    # test probe
    if test_probe:
        packet = etherbone.EtherbonePacket()
        packet.pf = 1
        dut.etherbone_model.send(packet)
        yield from self.etherbone_model.receive()
        print("probe: " + str(bool(self.etherbone_model.rx_packet.pr)))

    for i in range(8):
        # test writes
        if test_writes:
            writes_datas = [j for j in range(16)]
            writes = etherbone.EtherboneWrites(base_addr=0x1000,
                                               datas=writes_datas)
            record = etherbone.EtherboneRecord()
            record.writes = writes
            record.reads = None
            record.bca = 0
            record.rca = 0
            record.rff = 0
            record.cyc = 0
            record.wca = 0
            record.wff = 0
            record.byte_enable = 0xf
            record.wcount = len(writes_datas)
            record.rcount = 0

            packet = etherbone.EtherbonePacket()
            packet.records = [record]
            self.etherbone_model.send(packet)
            for i in range(256):
                yield

        # test reads
        if test_reads:
            reads_addrs = [0x1000 + 4*j for j in range(16)]
            reads = etherbone.EtherboneReads(base_ret_addr=0x1000,
                                             addrs=reads_addrs)
            record = etherbone.EtherboneRecord()
            record.writes = None
            record.reads = reads
            record.bca = 0
            record.rca = 0
            record.rff = 0
            record.cyc = 0
            record.wca = 0
            record.wff = 0
            record.byte_enable = 0xf
            record.wcount = 0
            record.rcount = len(reads_addrs)

            packet = etherbone.EtherbonePacket()
            packet.records = [record]
            self.etherbone_model.send(packet)
            yield from self.etherbone_model.receive()
            loopback_writes_datas = []
            loopback_writes_datas = self.etherbone_model.rx_packet.records.pop().writes.get_datas()

            # check resultss
            s, l, e = check(writes_datas, loopback_writes_datas)
            print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))

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
