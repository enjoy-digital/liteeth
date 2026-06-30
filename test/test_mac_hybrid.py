#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from litex.gen import *
from litex.gen.sim import *
from litex.soc.interconnect import stream

from liteeth.common import *
from liteeth.mac import LiteEthMACCoreCrossbar
from liteeth.mac.common import LiteEthMACCrossbar
from test.model import mac

# Helpers ------------------------------------------------------------------------------------------

class EndpointPair:
    def __init__(self, dw):
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

def mac_packet(target_mac, payload):
    packet               = mac.MACPacket(payload)
    packet.target_mac    = target_mac
    packet.sender_mac    = 0x90e2ba3c4d5e
    packet.ethernet_type = ethernet_type_ip
    packet.encode_header()
    return packet

def wait_signal(signal, cycles=128):
    for _ in range(cycles):
        if (yield signal):
            return
        yield
    raise TimeoutError

def send_packet(source, packet):
    for n, byte in enumerate(packet):
        last = n == (len(packet) - 1)
        yield source.valid.eq(1)
        yield source.data.eq(byte)
        yield source.last.eq(last)
        yield source.last_be.eq(1 if last else 0)
        for _ in range(128):
            yield
            if (yield source.ready):
                break
        else:
            raise TimeoutError
    yield source.valid.eq(0)
    yield source.last.eq(0)
    yield source.last_be.eq(0)
    yield

# DUT ----------------------------------------------------------------------------------------------

class DUT(LiteXModule):
    def __init__(self, dw=8, hw_mac=None):
        self.core      = EndpointPair(dw)
        self.interface = EndpointPair(dw)
        self.crossbar  = LiteEthMACCrossbar(dw)
        self.port      = self.crossbar.get_port(ethernet_type_ip, dw=dw)

        self.mac_crossbar = LiteEthMACCoreCrossbar(
            core      = self.core,
            crossbar  = self.crossbar,
            interface = self.interface,
            dw        = dw,
            hw_mac    = hw_mac,
        )

# Test Hybrid MAC ----------------------------------------------------------------------------------

class TestMACHybrid(unittest.TestCase):
    def test_rx_broadcast_valid_does_not_depend_on_own_ready(self):
        dut = DUT()

        def generator():
            yield dut.core.source.valid.eq(1)
            yield dut.core.source.data.eq(0x5a)
            yield dut.interface.sink.ready.eq(0)
            yield

            # CPU sink is not ready, so the source must not advance. Its valid
            # still needs to be presented since the other sink is ready.
            self.assertEqual((yield dut.core.source.ready), 0)
            self.assertEqual((yield dut.interface.sink.valid), 1)
            self.assertEqual((yield dut.mac_crossbar.depacketizer.sink.valid), 0)

            yield dut.interface.sink.ready.eq(1)
            yield

            self.assertEqual((yield dut.core.source.ready), 1)
            self.assertEqual((yield dut.interface.sink.valid), 1)
            self.assertEqual((yield dut.mac_crossbar.depacketizer.sink.valid), 1)

        run_simulation(dut, generator())

    def test_rx_local_unicast_ignores_stalled_cpu_path(self):
        hw_mac = 0x102030405060
        dut    = DUT(hw_mac=hw_mac)

        def generator():
            yield dut.interface.sink.ready.eq(0)
            yield dut.port.source.ready.eq(0)
            yield

            # Fill the CPU path with packets that are not selected for hardware.
            for n in range(5):
                packet = mac_packet(0x0a0b0c0d0e0f, [n])
                yield from send_packet(dut.core.source, packet)

            # A local-unicast packet only targets the hardware path and must not
            # be held off by the stalled CPU path.
            packet = mac_packet(hw_mac, [0x5a])
            yield from send_packet(dut.core.source, packet)

            yield from wait_signal(dut.port.source.valid)
            self.assertEqual((yield dut.port.source.data), 0x5a)

        run_simulation(dut, generator())
