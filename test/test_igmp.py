#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.gen.sim import *

from liteeth.common import *
from liteeth.core import LiteEthIPCore
from liteeth.core.igmp import LiteEthIGMPJoiner, igmp_checksum

from test.model import phy, mac, arp, ip

# Constants ----------------------------------------------------------------------------------------

ip_address  = 0xC0A80132      # 192.168.1.50.
mac_address = 0x10e2d5000001
ptp_groups  = [0xE0000181, 0xE0000182]

# Test IGMP Checksum -------------------------------------------------------------------------------

class TestIGMPChecksum(unittest.TestCase):
    """Verify IGMP checksum computation."""

    def test_checksum_verification(self):
        for group_ip in ptp_groups:
            cksum = igmp_checksum(group_ip)
            s = 0x1600 + cksum + ((group_ip >> 16) & 0xFFFF) + (group_ip & 0xFFFF)
            while s >> 16:
                s = (s & 0xFFFF) + (s >> 16)
            self.assertEqual(s, 0xFFFF)

# Test IGMP Joiner ---------------------------------------------------------------------------------

class TestIGMPJoiner(unittest.TestCase):
    """Verify IGMP reports don't hang the IP TX pipeline."""

    def test_single_group(self):
        self._run(groups=[0xE0000181], cycles=500)

    def test_two_groups(self):
        self._run(groups=ptp_groups, cycles=500)

    def _run(self, groups, cycles):
        class DUT(LiteXModule):
            def __init__(self):
                self.phy_model = phy.PHY(8, debug=False)
                self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
                self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
                self.ip_model  = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
                self.ip_core   = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000, with_icmp=False)
                self.igmp      = LiteEthIGMPJoiner(self.ip_core.ip, groups=groups, interval=0.0001, sys_clk_freq=100000)

        dut = DUT()
        def gen(dut):
            for _ in range(cycles):
                yield

        generators = {
            "sys":    [gen(dut)],
            "eth_tx": [dut.phy_model.phy_sink.generator(), dut.phy_model.generator()],
            "eth_rx": [dut.phy_model.phy_source.generator()],
        }
        clocks = {"sys": 10, "eth_rx": 10, "eth_tx": 10}
        run_simulation(dut, generators, clocks)

# Test IGMP Data ------------------------------------------------------------------------------------

class TestIGMPData(unittest.TestCase):
    """Capture PHY output and verify IGMP packet content."""

    def test_igmp_payload(self):
        class DUT(LiteXModule):
            def __init__(self):
                self.phy_model = phy.PHY(8, debug=False)
                self.mac_model = mac.MAC(self.phy_model, debug=False, loopback=False)
                self.arp_model = arp.ARP(self.mac_model, mac_address, ip_address, debug=False)
                self.ip_model  = ip.IP(self.mac_model, mac_address, ip_address, debug=False, loopback=False)
                self.ip_core   = LiteEthIPCore(self.phy_model, mac_address, ip_address, 100000, with_icmp=False)
                self.igmp      = LiteEthIGMPJoiner(self.ip_core.ip, groups=[0xE0000181], interval=0.0001, sys_clk_freq=100000)

        dut = DUT()
        captured_frames = []

        def capture_generator(dut):
            frame = []
            for _ in range(2000):
                valid = yield dut.phy_model.sink.valid
                ready = yield dut.phy_model.sink.ready
                if valid and ready:
                    data = yield dut.phy_model.sink.data
                    last = yield dut.phy_model.sink.last
                    frame.append(data)
                    if last:
                        captured_frames.append(list(frame))
                        frame = []
                yield

        def main_generator(dut):
            for _ in range(2000):
                yield

        generators = {
            "sys":    [main_generator(dut), capture_generator(dut)],
            "eth_tx": [dut.phy_model.phy_sink.generator(), dut.phy_model.generator()],
            "eth_rx": [dut.phy_model.phy_source.generator()],
        }
        clocks = {"sys": 10, "eth_rx": 10, "eth_tx": 10}
        run_simulation(dut, generators, clocks)

        # Should have captured at least one frame.
        self.assertGreater(len(captured_frames), 0, "No frames captured")

        # Find IP header (0x45) in first frame.
        frame = captured_frames[0]
        ip_start = None
        for i in range(len(frame) - 1):
            if frame[i] == 0x45:
                ip_start = i
                break
        self.assertIsNotNone(ip_start, f"No IP header in frame: {frame}")

        # Verify IP protocol = IGMP (2).
        ip_hdr = frame[ip_start:ip_start + 20]
        self.assertEqual(ip_hdr[9], 0x02, "Protocol should be IGMP")

        # Verify destination IP = 224.0.1.129.
        dst_ip = (ip_hdr[16] << 24) | (ip_hdr[17] << 16) | (ip_hdr[18] << 8) | ip_hdr[19]
        self.assertEqual(dst_ip, 0xE0000181)

        # Verify IGMP payload.
        igmp = frame[ip_start + 20:ip_start + 28]
        self.assertGreaterEqual(len(igmp), 8, f"IGMP too short: {igmp}")
        self.assertEqual(igmp[0], 0x16, f"IGMP type: got {igmp[0]:#04x}")

        # Verify IGMP group.
        grp = (igmp[4] << 24) | (igmp[5] << 16) | (igmp[6] << 8) | igmp[7]
        self.assertEqual(grp, 0xE0000181)

        # Verify checksum.
        cksum = (igmp[2] << 8) | igmp[3]
        self.assertEqual(cksum, igmp_checksum(0xE0000181))


if __name__ == "__main__":
    unittest.main()
