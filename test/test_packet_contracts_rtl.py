#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import shutil
import unittest
import tempfile
import subprocess
from pathlib import Path

from migen import *
from litex.gen.fhdl import verilog
from litex.gen import LiteXModule

from liteeth.common import *
from liteeth.fifo import PacketDropFIFO
from liteeth.core.udp import LiteEthUDPRX
from liteeth.frontend.etherbone import LiteEthEtherboneRecordReceiver
from test.test_packet_boundaries import packet_beats


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog required")
class TestPacketContractsRTL(unittest.TestCase):
    def check_rtl(self, dut, beats, expected):
        fields = list(expected[0])
        inputs = sorted(set().union(*(b.keys() for b in beats)))
        dut.clock_domains.cd_sys = ClockDomain("sys")
        ios = {dut.cd_sys.clk, dut.cd_sys.rst}
        ports = []
        declarations = []
        for direction, endpoint, names in [
            ("input", dut.sink, inputs + ["valid"]),
            ("input", dut.source, ["ready"]),
            ("output", dut.sink, ["ready"]),
            ("output", dut.source, fields + ["valid"]),
        ]:
            for name in names:
                signal = getattr(endpoint, name)
                pin = ("sink_" if endpoint is dut.sink else "source_") + name
                signal.name_override = pin
                ios.add(signal)
                ports.append(f".{pin}({pin})")
                declarations.append(f"{'reg' if direction == 'input' else 'wire'} [{len(signal)-1}:0] {pin};")
        assignments = []
        for beat in beats:
            assignments.append("@(negedge sys_clk);")
            assignments.append("sink_valid = 1;")
            assignments += [f"sink_{name} = {beat.get(name, 0)};" for name in inputs]
            assignments += ["@(posedge sys_clk);", "while (!sink_ready) @(posedge sys_clk);"]
        checks = []
        for index, beat in enumerate(expected):
            conditions = " || ".join(f"source_{field} !== {value}" for field, value in beat.items())
            checks.append(f'{index}: if ({conditions}) $fatal(1, "Output mismatch at beat {index}");')
        tb = '\n'.join(declarations) + f'''
reg sys_clk = 0;
reg sys_rst = 1;
always #5 sys_clk = ~sys_clk;
dut dut(.sys_clk(sys_clk), .sys_rst(sys_rst), {', '.join(ports)});
integer cycle = 0;
integer received = 0;
always @(negedge sys_clk) source_ready = cycle % 7 >= 3;
always @(posedge sys_clk) begin
    cycle <= cycle + 1;
    if (!sys_rst && source_valid && source_ready) begin
        case (received)
            {' '.join(checks)}
            default: $fatal(1, "Unexpected output beat");
        endcase
        received <= received + 1;
    end
    if (cycle == 1000) $fatal(1, "Timeout");
end
initial begin
    sink_valid = 0;
    source_ready = 0;
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    {' '.join(assignments)}
    @(negedge sys_clk);
    sink_valid = 0;
    repeat (100) @(posedge sys_clk);
    if (received != {len(expected)}) $fatal(1, "Missing output beats");
    $finish;
end
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/"dut.v").write_text(str(verilog.convert(dut, ios=ios, name="dut")))
            (path/"tb.v").write_text("module tb;\n" + tb + "\nendmodule\n")
            subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(path/"sim"),
                str(path/"dut.v"), str(path/"tb.v")], check=True, capture_output=True, timeout=30)
            result = subprocess.run(["vvp", str(path/"sim")], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_etherbone_malformed_then_mixed(self):
        words = [0x100, 0xdeadbeef, 0x800, 0x400]
        good = packet_beats(b"".join(w.to_bytes(4, "little") for w in words), 32,
            wcount=1, rcount=1, byte_enable=15)
        bad = packet_beats(bytes(80), 32, wcount=1, rcount=1, byte_enable=15)
        self.check_rtl(LiteEthEtherboneRecordReceiver(8), bad + good, [
            dict(we=1, addr=0x40, last=1), dict(we=0, addr=0x100, last=1)])

    def test_udp_empty_then_truncated(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                beats = []
                for length in [8, 16]:
                    header = bytes.fromhex("12345678") + length.to_bytes(2, "big") + bytes(2)
                    beats += packet_beats(header + bytes(3), dw, protocol=17, length=length)
                expected = [dict(be=1, error=0, last=0)]*2 + [dict(be=1, error=1, last=1)] if dw == 8 else [dict(be=7, error=7, last=1)]
                self.check_rtl(LiteEthUDPRX(0, dw), beats, expected)

    def test_fifo_discard_and_rewind(self):
        class DUT(LiteXModule):
            def __init__(self):
                self.fifo = fifo = PacketDropFIFO(eth_phy_description(32), 8)
                self.sink, self.source = fifo.sink, fifo.source
                self.comb += fifo.discard.eq(fifo.sink.error != 0)
        bad = packet_beats(bytes(16), 32, error=0)
        bad[1]["error"] = 1
        good = packet_beats(bytes.fromhex("efbeadde"), 32, error=0)
        self.check_rtl(DUT(), bad + good, [dict(data=0xdeadbeef, be=15, last=1)])
