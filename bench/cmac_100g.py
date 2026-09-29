#!/usr/bin/env python3
#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Generate a CMAC wrapper and a Vivado out-of-context synthesis project (no board pins)."""

import argparse
from pathlib import Path

from migen import ClockDomain, Record, Signal

from litex.gen.fhdl import verilog
from litex.build.xilinx.common import xilinx_special_overrides

from liteeth.phy.serial.baser.wrappers.usp_cmac import USP_CMAC_100G

# Generation ---------------------------------------------------------------------------------------

def generate(path, part="xcvu3p-ffvc1517-2-e", site="CMACE4_X0Y0", gt_group="X0Y0~X0Y3"):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    refclk   = Record([("p", 1), ("n", 1)], name="refclk")
    pads     = Record([(name, 4) for name in ["rxp", "rxn", "txp", "txn"]], name="qsfp")
    init_clk = Signal(name="init_clk")
    reset    = Signal(name="reset")
    dut = USP_CMAC_100G(refclk, pads, init_clk, reset)
    dut.clock_domains.cd_sys = ClockDomain("sys")
    ios = set(refclk.flatten() + pads.flatten() + dut.sink.flatten() + dut.source.flatten() +
        [init_clk, reset, dut.cd_sys.clk, dut.cd_sys.rst, dut.link_up,
         dut.rx_packets, dut.rx_drops, dut.rx_bad_frames, dut.tx_underflow, dut.tx_overflow])
    verilog.convert(dut,
        ios               = ios,
        name              = "cmac_adapter",
        special_overrides = xilinx_special_overrides,
        comb_cycle_policy = "error",
    ).write(str(path/"cmac_adapter.v"))
    # Keep generated IP outside the repository. Tcl list quoting preserves paths containing spaces.
    script = Path(__file__).with_suffix(".tcl").resolve()
    (path/"build.tcl").write_text(f"""set_param general.maxThreads 4
create_project cmac_check {{project}} -part {{{part}}} -force
set cmac_name liteeth_cmac
set cmac_site {{{site}}}
set cmac_gt_group {{{gt_group}}}
source {{{script}}}
read_verilog cmac_adapter.v
set_property generate_synth_checkpoint false [get_files -all */liteeth_cmac.xci]
synth_design -top cmac_adapter -part {{{part}}} -mode out_of_context
report_utilization -file utilization.rpt
report_drc -file drc.rpt
write_checkpoint -force synthesized.dcp
""")
    return path

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="build/cmac_100g")
    parser.add_argument("--part",       default="xcvu3p-ffvc1517-2-e")
    parser.add_argument("--site",       default="CMACE4_X0Y0")
    parser.add_argument("--gt-group",   default="X0Y0~X0Y3")
    args = parser.parse_args()
    generate(args.output_dir,
        part     = args.part,
        site     = args.site,
        gt_group = args.gt_group,
    )


if __name__ == "__main__":
    main()
