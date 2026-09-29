#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from liteeth.core.ip import LiteEthIPRX
from test.rtl_helpers import rtl_available, run_iverilog
from test.test_packet_boundaries import packet_beats
from test.test_ip_rx_validation import wire_packet


@unittest.skipUnless(rtl_available, "Icarus Verilog required")
class TestIPRXValidationRTL(unittest.TestCase):
    def test_invalid_length_and_recovery(self):
        for dw in [8, 32, 64]:
            with self.subTest(dw=dw):
                dut = LiteEthIPRX(0, 0xc0a80132, dw=dw)
                actions = []
                for length in [0, 19, 20, 24]:
                    for beat in packet_beats(wire_packet(length), dw):
                        actions += ["@(negedge sys_clk); sink_valid = 1;"]
                        actions += [f"sink_{name} = {value};" for name, value in beat.items()]
                        actions += ["@(posedge sys_clk); while (!sink_ready) @(posedge sys_clk);"]
                expected = packet_beats(b"test", dw)
                checks = []
                for n, beat in enumerate(expected):
                    mask = (1 << (8*min(dw//8, 4))) - 1
                    checks.append(f'''{n}: if (source_be != {beat['be']} || source_last != {beat['last']} ||
                        (source_data & {dw}'h{mask:x}) != {beat['data']}) $fatal(1, "Bad payload");''')
                run_iverilog(dut, {
                    "sink_valid": dut.sink.valid, "sink_data": dut.sink.data,
                    "sink_be": dut.sink.be, "sink_last": dut.sink.last, "source_ready": dut.source.ready,
                }, {
                    "sink_ready": dut.sink.ready, "source_valid": dut.source.valid,
                    "source_data": dut.source.data, "source_be": dut.source.be,
                    "source_last": dut.source.last, "source_length": dut.source.length,
                }, f'''
integer received = 0;
always @(negedge sys_clk) source_ready = cycles % 5 >= 2;
always @(posedge sys_clk) begin
    if (!sys_rst && source_valid && source_ready) begin
        if (source_length != 4) $fatal(1, "Invalid length escaped validation");
        case (received)
            {' '.join(checks)}
            default: $fatal(1, "Unexpected output");
        endcase
        received <= received + 1;
    end
end
initial begin
    repeat (4) @(negedge sys_clk);
    sys_rst = 0;
    {' '.join(actions)}
    @(negedge sys_clk);
    sink_valid = 0;
    repeat (30) @(negedge sys_clk);
    if (received != {len(expected)}) $fatal(1, "Valid packet did not recover");
    $finish;
end
''')
