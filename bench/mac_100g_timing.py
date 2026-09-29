#!/usr/bin/env python3
#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Generate a Vivado OOC timing experiment for the full MAC-adapter/UDP path, without CMAC."""

import argparse
from pathlib import Path

from bench.mac_100g import generate

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",   default="build/mac_100g_timing")
    parser.add_argument("--part",         default="xcvu3p-ffvc1517-2-e")
    parser.add_argument("--sys-clk-freq", type=float, default=250e6)
    args = parser.parse_args()
    path = generate(args.output_dir, [18, 1472], sys_clk_freq=args.sys_clk_freq)
    (path/"timing.tcl").write_text(f"""set_param general.maxThreads 4
read_verilog dut.v
synth_design -top dut -part {{{args.part}}} -mode out_of_context
create_clock -name sys -period {1e9/args.sys_clk_freq:.6f} [get_ports sys_clk]
create_clock -name eth_tx -period {1e9/322.265625e6:.6f} [get_ports eth_tx_clk]
create_clock -name eth_rx -period {1e9/322.265625e6:.6f} [get_ports eth_rx_clk]
set_clock_groups -asynchronous -group [get_clocks sys] -group [get_clocks eth_tx] -group [get_clocks eth_rx]
opt_design
place_design
phys_opt_design
route_design
report_timing_summary -file timing.rpt
report_utilization -file utilization.rpt
report_cdc -file cdc.rpt
write_checkpoint -force routed.dcp
""")
    print(f"Run vivado -mode batch -source timing.tcl from {Path(path).resolve()}")


if __name__ == "__main__":
    main()
