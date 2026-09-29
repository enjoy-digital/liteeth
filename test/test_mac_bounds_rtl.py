#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from liteeth.mac.packet import LiteEthMACPacketWriter
from liteeth.mac.sram import LiteEthMACSRAMReader
from test.rtl_helpers import rtl_available, run_iverilog


@unittest.skipUnless(rtl_available, "Icarus Verilog required")
class TestMACBoundsRTL(unittest.TestCase):
    def test_write_capacity_and_recovery(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                dut = LiteEthMACPacketWriter(dw, depth=3)
                run_iverilog(dut, {
                    "sink_valid": dut.sink.valid, "sink_last": dut.sink.last,
                    "sink_be": dut.sink.be, "source_ready": dut.source.ready,
                }, {
                    "sink_ready": dut.sink.ready, "source_valid": dut.source.valid,
                    "offset": dut.offset, "done": dut.done, "drop": dut.drop,
                }, f'''
integer writes = 0;
integer drops = 0;
integer completed = 0;
integer n;
always @(posedge sys_clk) begin
    if (!sys_rst) begin
        if (source_valid && offset >= {3*dw//8}) $fatal(1, "Out-of-range write");
        if (source_valid && source_ready) writes <= writes + 1;
        if (drop) drops <= drops + 1;
        if (done) completed <= completed + 1;
    end
end
always @(negedge sys_clk) source_ready = cycles % 4 >= 2;
initial begin
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    sink_be = {(1 << (dw//8)) - 1};
    for (n = 0; n < 20; n = n + 1) begin
        @(negedge sys_clk);
        sink_valid = 1;
        sink_last = n == 19;
        @(posedge sys_clk);
        while (!sink_ready) @(posedge sys_clk);
    end
    @(negedge sys_clk);
    sink_valid = 0;
    repeat (4) @(negedge sys_clk);
    sink_valid = 1;
    sink_last = 1;
    @(posedge sys_clk);
    while (!sink_ready) @(posedge sys_clk);
    @(negedge sys_clk);
    sink_valid = 0;
    repeat (6) @(negedge sys_clk);
    if (writes != 4 || drops != 1 || completed != 1) $fatal(1, "Bad completion/write counts");
    $finish;
end
''')

    def test_invalid_tx_command_then_valid(self):
        dut = LiteEthMACSRAMReader(32, 3, nslots=3)
        dut.specials += dut.mems
        run_iverilog(dut, {
            "start": dut._start.wr_stb, "slot": dut._slot.storage,
            "length": dut._length.storage, "source_ready": dut.source.ready,
        }, {
            "source_valid": dut.source.valid, "source_be": dut.source.be,
            "source_last": dut.source.last, "completed": dut.ev.done.trigger,
        }, '''
integer transfers = 0;
integer completions = 0;
always @(posedge sys_clk) begin
    if (!sys_rst) begin
        if (source_valid && source_ready) begin
            if (source_be != 7 || !source_last) $fatal(1, "Bad valid packet");
            transfers <= transfers + 1;
        end
        if (completed) completions <= completions + 1;
    end
end
task command(input integer s, input integer l);
    begin
        @(negedge sys_clk);
        slot = s;
        length = l;
        start = 1;
        @(negedge sys_clk);
        start = 0;
        repeat (12) @(negedge sys_clk);
    end
endtask
initial begin
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    source_ready = 1;
    command(0, 0);
    command(0, 13);
    command(3, 4);
    command(2, 3);
    if (transfers != 1 || completions != 4) $fatal(1, "Invalid commands emitted data or blocked the queue");
    $finish;
end
''')
