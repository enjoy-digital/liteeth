#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from test.stream_helpers import *

from liteeth.common import *
from liteeth.core import LiteEthUDPIPCore

from test.model import phy, mac, arp, ip, udp

from litex.gen.sim import *

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0x12345678
mac_address = 0x12345678abcd

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw=8, eth_mtu=eth_mtu_default):
        self.dw        = dw
        self.phy_model = phy.PHY(8, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.ip_model  = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
        self.udp_model = udp.UDP(self.ip_model, ip_address, debug=False, loopback=True)

        self.core     = LiteEthUDPIPCore(self.phy_model, mac_address, ip_address, 100000, eth_mtu=eth_mtu)
        udp_port      = self.core.udp.crossbar.get_port(0x5678, dw)
        self.streamer = PacketStreamer(eth_udp_user_description(dw))
        self.logger   = PacketLogger(eth_udp_user_description(dw))
        self.comb += [
            Record.connect(self.streamer.source, udp_port.sink),
            udp_port.sink.ip_address.eq(0x12345678),
            udp_port.sink.src_port.eq(0x1234),
            udp_port.sink.dst_port.eq(0x5678),
            udp_port.sink.length.eq(64//(dw//8)),
            Record.connect(udp_port.source, self.logger.sink)
        ]

# DUT (64-bit / Jumbo) -----------------------------------------------------------------------------

class DUT64(LiteXModule):
    """64-bit core and user port with jumbo MTU, byte-granular (last_be) packets."""
    def __init__(self, packet_length, eth_mtu=eth_mtu_jumboframe, dw=64):
        self.dw        = dw
        # 64-bit PHY model (8x fewer cycles than the byte model).
        self.phy_model = phy.PHY(dw, debug=False)
        self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
        self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
        self.ip_model  = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
        self.udp_model = udp.UDP(self.ip_model, ip_address, debug=False, loopback=True)

        self.core     = LiteEthUDPIPCore(self.phy_model, mac_address, ip_address, 100000,
            dw      = dw,
            eth_mtu = eth_mtu,
        )
        udp_port      = self.core.udp.crossbar.get_port(0x5678, dw)
        self.streamer = PacketStreamer(eth_udp_user_description(dw), byte_data=True)
        self.logger   = PacketLogger(eth_udp_user_description(dw),   byte_data=True)
        self.comb += [
            Record.connect(self.streamer.source, udp_port.sink),
            udp_port.sink.ip_address.eq(0x12345678),
            udp_port.sink.src_port.eq(0x1234),
            udp_port.sink.dst_port.eq(0x5678),
            udp_port.sink.length.eq(packet_length),
            Record.connect(udp_port.source, self.logger.sink)
        ]
        # Sticky error flag of the received packets (the MAC RX padding checker flags frames longer
        # than eth_mtu as errors on their last word).
        self.rx_error = Signal()
        source        = udp_port.source
        self.sync += If(source.valid & source.ready & (source.error != 0),
            self.rx_error.eq(1)
        )

# Generator ----------------------------------------------------------------------------------------

def main_generator(dut):
    packet = Packet([i for i in range(64//(dut.dw//8))])
    dut.streamer.send(packet)
    yield from dut.logger.receive()

    # check results
    s, l, e = check(packet, dut.logger.packet)
    print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))

# Test UDP -----------------------------------------------------------------------------------------

class TestUDP(unittest.TestCase):
    def test(self):
        for mtu in [eth_mtu_default, eth_mtu_jumboframe]:
            with self.subTest(eth_mtu=mtu):
                dut = DUT(8, eth_mtu=mtu)
                generators = {
                    "sys"    : [
                        main_generator(dut),
                        dut.streamer.generator(),
                        dut.logger.generator(),
                    ],
                    "eth_tx" : [
                        dut.phy_model.phy_sink.generator(),
                        dut.phy_model.generator(),
                    ],
                    "eth_rx" : [
                        dut.phy_model.phy_source.generator()
                    ]
                }
                clocks = {
                    "sys"    : 10,
                    "eth_rx" : 10,
                    "eth_tx" : 10,
                }
                run_simulation(dut, generators, clocks, vcd_name="sim.vcd")

    def test_64bit_jumbo(self):
        # 64-bit datapath with jumbo MTU: byte-granular (last_be) and jumbo-sized UDP packets are
        # looped back by the UDP model and must come back intact without error flag. 2060 bytes is
        # above the standard MTU (the RX padding checker length counter, sized from eth_mtu, wraps
        # with eth_mtu_default). Larger packets are kept out of the unit tests (the Python packet
        # models are slow) and covered by the hardware/Verilator benches.
        for length in [77, 2060]:
            with self.subTest(length=length):
                dut    = DUT64(packet_length=length)
                packet = Packet([(i*7 + 1) & 0xff for i in range(length)])
                result = {}

                def generator(dut):
                    dut.streamer.send(packet)
                    yield from dut.logger.receive()
                    result["packet"]   = list(dut.logger.packet)
                    result["rx_error"] = (yield dut.rx_error)

                generators = {
                    "sys"    : [
                        generator(dut),
                        dut.streamer.generator(),
                        dut.logger.generator(),
                    ],
                    "eth_tx" : [
                        dut.phy_model.phy_sink.generator(),
                        dut.phy_model.generator(),
                    ],
                    "eth_rx" : [
                        dut.phy_model.phy_source.generator()
                    ]
                }
                clocks = {
                    "sys"    : 10,
                    "eth_rx" : 10,
                    "eth_tx" : 10,
                }
                run_simulation(dut, generators, clocks)
                self.assertEqual(result["packet"], list(packet))
                self.assertEqual(result["rx_error"], 0)
