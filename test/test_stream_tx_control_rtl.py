#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from liteeth.frontend.stream import LiteEthStream2UDPTX
from test.rtl_helpers import rtl_available, run_iverilog


@unittest.skipUnless(rtl_available, "Icarus Verilog required")
class TestStreamTXControlRTL(unittest.TestCase):
    def test_unbuffered_control_changes_while_stalled(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                dut = LiteEthStream2UDPTX(data_width=dw, with_be=True)
                run_iverilog(dut, {
                    "enable": dut.enable, "ip": dut.ip_address, "port": dut.udp_port,
                    "sink_valid": dut.sink.valid, "sink_data": dut.sink.data,
                    "sink_be": dut.sink.be, "source_ready": dut.source.ready,
                }, {
                    "sink_ready": dut.sink.ready, "source_valid": dut.source.valid,
                    "source_data": dut.source.data, "source_be": dut.source.be,
                    "source_last": dut.source.last, "source_length": dut.source.length,
                    "source_ip": dut.source.ip_address, "source_port": dut.source.dst_port,
                }, '''
integer transfers = 0;
always @(posedge sys_clk) begin
    if (!sys_rst && source_valid && source_ready) begin
        if (source_be != 1 || source_length != 1 || !source_last) $fatal(1, "Invalid framing");
        case (transfers)
            0: if (source_ip != 1 || source_port != 100 || source_data != 17) $fatal(1, "First packet changed");
            1: if (source_ip != 2 || source_port != 200 || source_data != 18) $fatal(1, "Next packet lost configuration");
            default: $fatal(1, "Unexpected packet");
        endcase
        transfers <= transfers + 1;
    end
end
initial begin
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    sink_valid = 1;
    sink_data = 17;
    sink_be = 1;
    ip = 1;
    port = 100;
    repeat (3) begin
        @(negedge sys_clk);
        if (source_valid || sink_ready) $fatal(1, "Disabled TX accepted or sent a packet");
    end
    enable = 1;
    repeat (3) @(negedge sys_clk);
    ip = 2;
    port = 200;
    enable = 0;
    repeat (4) begin
        @(negedge sys_clk);
        if (!source_valid || source_ip != 1 || source_port != 100) $fatal(1, "Stalled packet changed");
    end
    source_ready = 1;
    @(negedge sys_clk);
    sink_data = 18;
    repeat (3) begin
        @(negedge sys_clk);
        if (source_valid || sink_ready) $fatal(1, "Next packet started while disabled");
    end
    enable = 1;
    @(negedge sys_clk);
    sink_valid = 0;
    repeat (3) @(negedge sys_clk);
    if (transfers != 2) $fatal(1, "Missing packets");
    $finish;
end
''')
