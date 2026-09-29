#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import shutil
import tempfile
import subprocess
from pathlib import Path

from migen import ClockDomain
from litex.gen.fhdl import verilog


rtl_available = bool(shutil.which("iverilog") and shutil.which("vvp"))


def run_iverilog(dut, inputs, outputs, bench, cycles=1000):
    """Run a bounded testbench against LiteX-generated Verilog, with an explicit pin mapping."""
    dut.clock_domains.cd_sys = ClockDomain("sys")
    ios = {dut.cd_sys.clk, dut.cd_sys.rst}
    ports = [".sys_clk(sys_clk)", ".sys_rst(sys_rst)"]
    declarations = []
    for pins, kind in [(inputs, "reg"), (outputs, "wire")]:
        for name, signal in pins.items():
            signal.name_override = name
            ios.add(signal)
            ports.append(f".{name}({name})")
            initial = " = 0" if kind == "reg" else ""
            declarations.append(f"{kind} [{len(signal)-1}:0] {name}{initial};")
    header = f'''
module tb;
reg sys_clk = 0;
reg sys_rst = 1;
always #5 sys_clk = ~sys_clk;
integer cycles = 0;
always @(posedge sys_clk) begin
    cycles <= cycles + 1;
    if (cycles == {cycles}) $fatal(1, "Simulation timed out");
end
{chr(10).join(declarations)}
dut dut({', '.join(ports)});
'''
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        conversion = verilog.convert(dut, ios=ios, name="dut", comb_cycle_policy="error")
        conversion.write(str(path/"dut.v"))
        (path/"tb.v").write_text(header + bench + "\nendmodule\n")
        result = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", "sim", "dut.v", "tb.v"],
            cwd=directory, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        result = subprocess.run(["vvp", "sim"], cwd=directory,
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
