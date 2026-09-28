#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import zlib
import unittest
from types import SimpleNamespace

from migen import ClockDomainsRenamer, Instance, Record

from litex.gen import LiteXModule
from litex.gen.sim import passive, run_simulation
from litex.build.io import DDRInput
from litex.soc.interconnect import stream

from liteeth.common import eth_phy_description
from liteeth.mac.core import LiteEthMACCore
from liteeth.mac.packet import LiteEthMACPacketWriter
from liteeth.phy.parallel.rgmii import gw5, s6, s7, us
from liteeth.phy.parallel.rgmii.ecp5 import LiteEthRGMIIRXDatapath


class TestRGMIIRX(unittest.TestCase):
    def test_error_follows_its_byte_through_each_vendor_pipeline(self):
        for family in (s6, s7, us, gw5):
            for error_byte in (None, 0, 1, 2):
                with self.subTest(family=family.__name__, error_byte=error_byte):
                    pads = Record([("rx_ctl", 1), ("rx_data", 4)])
                    dut = family.LiteEthPHYRGMIIRX(pads)
                    fragment = dut.get_fragment()
                    captures = []
                    for special in sorted(fragment.specials, key=lambda s: s.duid):
                        if isinstance(special, DDRInput):
                            captures.append((special.o1, special.o2))
                        elif isinstance(special, Instance) and special.of in ("IDDR2", "IDDR", "IDDRE1"):
                            ports = {item.name: item.expr for item in special.items
                                if isinstance(item, Instance.Output)}
                            names = ("Q0", "Q1") if special.of == "IDDR2" else ("Q1", "Q2")
                            self.assertTrue(all(name in ports for name in names))
                            captures.append(tuple(ports[name] for name in names))
                    self.assertEqual(len(captures), 5) # Control, then data bits 0..3.
                    # Exercise the actual fabric pipeline by driving its captured I/O samples.
                    # The vendor delay/DDR primitives themselves are not simulated here.
                    fragment.specials.clear()
                    data = [0x5a, 0xc3, 0x1e]
                    errors = [int(i == error_byte) for i in range(len(data))]
                    rows = [(0, 0)]*4
                    rows += [(value, 1 | ((1 ^ error) << 1)) for value, error in zip(data, errors)]
                    rows += [(0x0e, 0b10), (0, 0)]*2 # Idle error indications must not create data.
                    rows += [(value, 0b11) for value in data] + [(0, 0)]*4
                    observed = []

                    def driver():
                        previous = (0, 0)
                        for value, control in rows:
                            # IDDR2 C0 alignment delays only Q1. The existing S6 fabric register
                            # delays Q0 by one cycle to pair the same byte's two nibbles.
                            high, ctl_high = previous if family is s6 else (value, control)
                            yield captures[0][0].eq(control & 1)
                            yield captures[0][1].eq((ctl_high >> 1) & 1)
                            for bit, (rising, falling) in enumerate(captures[1:]):
                                yield rising.eq((value >> bit) & 1)
                                yield falling.eq((high >> (bit + 4)) & 1)
                            previous = (value, control)
                            yield
                        for _ in range(4):
                            yield

                    @passive
                    def monitor():
                        while True:
                            if (yield dut.source.valid):
                                observed.append(((yield dut.source.data),
                                    (yield dut.source.error), (yield dut.source.last)))
                            yield

                    run_simulation(fragment, [driver(), monitor()])
                    expected = [(value, int(error_byte is not None and i >= error_byte),
                        int(i == len(data) - 1)) for i, value in enumerate(data)]
                    expected += [(value, 0, int(i == len(data) - 1)) for i, value in enumerate(data)]
                    self.assertEqual(observed, expected)


class RGMIIReceiveDUT(LiteXModule):
    def __init__(self, dw, with_sys_datapath=False):
        self.rx = ClockDomainsRenamer("eth_rx")(LiteEthRGMIIRXDatapath())
        phy = SimpleNamespace(dw=8, source=self.rx.source,
            sink=stream.Endpoint(eth_phy_description(8)))
        self.core = LiteEthMACCore(phy, dw, with_store_and_forward=False,
            with_sys_datapath=with_sys_datapath)
        self.writer = LiteEthMACPacketWriter(dw, depth=128)
        self.comb += [
            phy.sink.ready.eq(1),
            self.core.source.connect(self.writer.sink),
            self.writer.source.ready.eq(1),
        ]


class TestRGMIIMACErrors(unittest.TestCase):
    def check_rx_error_frames(self, widths, error_bytes=(None, 0, 7, 8, 30, -1), with_sys_datapath=False):
        payload = bytes(range(60))
        frame = bytes([0x55]*7 + [0xd5]) + payload + zlib.crc32(payload).to_bytes(4, "little")
        for dw in widths:
            for error_byte in error_bytes:
                if error_byte == -1:
                    error_byte = len(frame) - 1
                with self.subTest(dw=dw, error_byte=error_byte):
                    dut = RGMIIReceiveDUT(dw, with_sys_datapath=with_sys_datapath)
                    results = []

                    def driver():
                        for _ in range(16):
                            yield
                        # The next clean frame must still be accepted after an errored frame.
                        for inject in (True, False):
                            for i, value in enumerate(frame):
                                yield dut.rx.rx_ctl.eq(0b01 if inject and i == error_byte else 0b11)
                                yield dut.rx.rx_data.eq(value)
                                yield
                            yield dut.rx.rx_ctl.eq(0)
                            for _ in range(64):
                                yield
                        for _ in range(64):
                            yield

                    @passive
                    def monitor():
                        while True:
                            if (yield dut.writer.done):
                                results.append("accepted")
                            if (yield dut.writer.drop):
                                results.append("dropped")
                            yield

                    run_simulation(dut, {"eth_rx": driver(), "sys": monitor()}, clocks={
                        "eth_rx" : 10,
                        "eth_tx" : 10,
                        "sys"    : 10,
                    })
                    self.assertEqual(results, ["accepted" if error_byte is None else "dropped", "accepted"])

    def test_rx_error_rejects_frame_even_with_valid_fcs(self):
        self.check_rx_error_frames((8, 32, 64))

    def test_rx_error_with_system_clocked_mac(self):
        self.check_rx_error_frames((64,), error_bytes=(None, 30, -1), with_sys_datapath=True)
