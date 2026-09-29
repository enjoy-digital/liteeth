#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import shutil
import unittest

from migen import ClockDomainsRenamer
from litex.gen import LiteXModule
from liteeth.common import stream, eth_phy_description
from liteeth.core import LiteEthUDPIPCore
from bench.udp_throughput import UDPPath
from test.rtl_helpers import rtl_available, run_rtl


class FullCoreLoopback(LiteXModule):
    def __init__(self, width):
        phy = LiteXModule()
        phy.dw = 64
        phy.tx_clk_freq = phy.rx_clk_freq = 390.625e6
        phy.sink = stream.Endpoint(eth_phy_description(64))
        phy.source = stream.Endpoint(eth_phy_description(64))
        phy.sync += phy.sink.connect(phy.source, omit={"ready"})
        phy.comb += phy.sink.ready.eq(1)
        self.phy = phy
        self.core = core = LiteEthUDPIPCore(phy, 0x10e2d5000000, 0xc0a80132, 200e6,
            dw=width, eth_mtu=9022, with_icmp=False)
        port = core.udp.crossbar.get_port(2000, dw=width)
        self.sink = port.sink
        self.source = port.source


class TestWideDatapathRTL(unittest.TestCase):
    @unittest.skipUnless(rtl_available, "Icarus Verilog is required")
    def test_changing_packets_and_stalls(self):
        for width in [128, 256, 512]:
            with self.subTest(width=width):
                self.check_path(UDPPath(width), width, [1, 65, 18, 129, 3, 256, 33])

    @unittest.skipUnless(shutil.which("verilator"), "Verilator 5 is required")
    def test_full_mac_core(self):
        for width in [128, 256, 512]:
            with self.subTest(width=width):
                # Keep CDC FIFOs in the netlist; a common test clock isolates functional behavior.
                dut = ClockDomainsRenamer({"eth_tx": "sys", "eth_rx": "sys"})(FullCoreLoopback(width))
                self.check_path(dut, width, [1, 65, 1472, 8972, 3], destination=0xc0a801ff, simulator="verilator")

    def check_path(self, dut, width, lengths, destination=0xc0a80132, simulator="iverilog"):
        lanes = width//8
        commands, expected = [], []
        for n, length in enumerate(lengths):
            payload = bytes((i + n) % 256 for i in range(length))
            for offset in range(0, length, lanes):
                word = payload[offset:offset + lanes]
                value = int.from_bytes(word, 'little')
                mask = (1 << len(word)) - 1
                last = int(offset + lanes >= length)
                commands.append(f"send({width}'h{value:x}, {lanes}'h{mask:x}, {last}, {length}, {1000+n});")
                expected.append((value, mask, last, length, 1000 + n))
        checks = []
        for i, (value, mask, last, length, port) in enumerate(expected):
            data_mask = (1 << (8*bin(mask).count("1"))) - 1
            checks.append(f"""{i}: if ((o_data & {width}'h{data_mask:x}) !== {width}'h{value:x} ||
                o_be !== {lanes}'h{mask:x} || o_last !== 1'b{last} || o_length !== 16'd{length} ||
                o_src_port !== 16'd{port} || o_dst_port !== 16'd2000 ||
                o_ip_address !== 32'hc0a80132 || (o_error & o_be) !== 0)
                $fatal(1, "Incorrect output beat {i}");""")
        inputs = {"i_" + name: getattr(dut.sink, name)
            for name in ['valid', 'data', 'be', 'last', 'length', 'src_port', 'dst_port', 'ip_address']}
        inputs['o_ready'] = dut.source.ready
        outputs = {"o_" + name: getattr(dut.source, name)
            for name in ['valid', 'data', 'be', 'last', 'length', 'src_port', 'dst_port', 'ip_address', 'error']}
        outputs['i_ready'] = dut.sink.ready
        run_rtl(dut, inputs, outputs, f'''
integer received = 0;
always @(negedge sys_clk) o_ready = !sys_rst && (cycles % 5 != 0) && (cycles % 5 != 1);
always @(posedge sys_clk) if (!sys_rst && o_valid && o_ready) begin
    case (received)
{chr(10).join(checks)}
default: $fatal(1, "Unexpected output");
    endcase
    received <= received + 1;
end
task send(input [{width-1}:0] data, input [{lanes-1}:0] be, input last,
    input [15:0] length, input [15:0] port);
begin
    @(negedge sys_clk);
    i_data = data; i_be = be; i_last = last; i_length = length; i_src_port = port;
    i_dst_port = 2000; i_ip_address = 32'h{destination:08x}; i_valid = 1;
    @(posedge sys_clk);
    while (!i_ready) @(posedge sys_clk);
    @(negedge sys_clk); i_valid = 0;
end
endtask
initial begin
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    {chr(10).join(commands)}
    wait (received == {len(expected)});
    repeat (10) @(posedge sys_clk);
    $finish;
end
''', cycles=15000, simulator=simulator)
