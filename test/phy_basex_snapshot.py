#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

"""Capture BASE-X hardware before/after a refactor (requires Yosys).

Run this script from each checkout with a different --output-dir. Compare the
summary.json files; retain the Verilog and Yosys JSON for investigating changes.
Names and source locations are excluded from the structural fingerprint, but
cell parameters, port bit order, connectivity, constants and register init are
included. This is a regression check, not a formal equivalence proof.
"""

import io
import sys
import json
import hashlib
import argparse
import importlib
import subprocess
from pathlib import Path
from contextlib import redirect_stdout

# Select the checkout being inspected, even when this script lives elsewhere.
sys.path.insert(0, str(Path.cwd()))

from migen import ClockDomain, Record, Signal
from migen.fhdl import verilog
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.build.xilinx.common import XilinxAsyncResetSynchronizer

from liteeth.phy.serial.gtp_7series import QPLLChannel


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fingerprint(module):
    # Label a bipartite graph of cells and nets. Port names/indices distinguish
    # connections; top-level names anchor the externally visible interface.
    labels = {}
    edges = {}

    def node(key, label):
        labels[key] = label
        edges.setdefault(key, [])

    def connect(a, b, label):
        edges[a].append((label, b))
        edges[b].append((label, a))

    bits = set()
    for cell in module["cells"].values():
        for connection in cell["connections"].values():
            bits.update(connection)
    for port in module["ports"].values():
        bits.update(port["bits"])
    for bit in bits:
        node(("bit", bit), ["constant", bit] if isinstance(bit, str) else ["net"])
    for name, port in module["ports"].items():
        for index, bit in enumerate(port["bits"]):
            key = ("port", name, index)
            node(key, ["port", name, index, port["direction"]])
            connect(key, ("bit", bit), ["port", 0])
    for name, cell in module["cells"].items():
        key = ("cell", name)
        parameters = dict(cell["parameters"])
        if cell["type"].startswith("$mem"):
            parameters.pop("MEMID", None)
        node(key, ["cell", cell["type"], parameters])
        for port, connection in cell["connections"].items():
            for index, bit in enumerate(connection):
                # Yosys can reorder reduction inputs when internal names change.
                # These bits are commutative; all other port bit order is significant.
                if port == "A" and cell["type"] in (
                    "$reduce_and", "$reduce_or", "$reduce_xor", "$reduce_xnor", "$reduce_bool"):
                    index = 0
                connect(key, ("bit", bit), [port, index])
    initial = {}
    for net in module["netnames"].values():
        if "init" in net["attributes"]:
            for bit, value in zip(net["bits"], reversed(net["attributes"]["init"])):
                initial[bit] = value
    for bit, value in initial.items():
        if ("bit", bit) in labels:
            labels[("bit", bit)].append(["init", value])
    colors = {key: digest(label) for key, label in labels.items()}
    for _ in range(16):
        colors = {key: digest([colors[key], sorted(
            (label, colors[other]) for label, other in edges[key])]) for key in colors}
    return digest(sorted(colors.values()))


def variants():
    for device, prefix in [
        ("a7_gtp", "A7"), ("k7_gtx", "K7"), ("v7_gth", "V7"),
        ("ku_gth", "KU"), ("usp_gth", "USP_GTH"), ("usp_gty", "USP_GTY"),
    ]:
        module = importlib.import_module("liteeth.phy.serial.basex.wrappers." + device)
        for rate in (1000, 2500):
            cls = getattr(module, f'{prefix}_{rate}BASEX')
            if device == "a7_gtp":
                for channel in (0, 1):
                    for cm in ("PLL", "MMCM"):
                        yield f'{device}_{rate}_qpll{channel}_{cm}', cls, dict(
                            channel=channel, tx_cm_type=cm, rx_cm_type=cm,
                            with_pcs_buffers=(cm == "MMCM"))
                    yield f'{device}_{rate}_qpll{channel}_mixed', cls, dict(
                        channel=channel, tx_cm_type="MMCM", rx_cm_type="PLL",
                        tx_cm_buf_type="BUFG", rx_cm_buf_type="BUFH",
                        tx_polarity=1, rx_polarity=1)
            else:
                refs = (200e6,) if rate == 1000 else (156.25e6,)
                if device == "k7_gtx" and rate == 2500:
                    refs = (125e6,)
                elif device != "k7_gtx" and rate == 1000:
                    refs = (200e6, 156.25e6)
                for refclk in refs:
                    yield f'{device}_{rate}_{int(refclk)}', cls, dict(refclk_freq=refclk)
                    yield f'{device}_{rate}_{int(refclk)}_differential', cls, dict(
                        refclk_freq=refclk, differential=True, tx_polarity=1, rx_polarity=1)
                    if device.startswith("usp_"):
                        yield f'{device}_{rate}_{int(refclk)}_fabric', cls, dict(
                            refclk_freq=refclk, refclk_from_fabric=True)


def capture(cls, kwargs, destination):
    kwargs = dict(kwargs)
    pads = Record([("txp", 1), ("txn", 1), ("rxp", 1), ("rxn", 1)])
    if "channel" in kwargs:
        refclk = QPLLChannel(kwargs.pop("channel"))
        ref_ios = {refclk.clk, refclk.refclk, refclk.lock, refclk.reset}
        for name in ("clk", "refclk", "lock", "reset"):
            getattr(refclk, name).name_override = "qpll_" + name
    else:
        if kwargs.pop("differential", False):
            refclk = Record([("p", 1), ("n", 1)], name="refclk")
            ref_ios = set(refclk.flatten())
        else:
            refclk = Signal(name_override="refclk")
            ref_ios = {refclk}
    phy = cls(refclk, pads, 100e6, with_csr=False, **kwargs)
    phy.cd_sys = ClockDomain("sys")
    ios = ref_ios | set(pads.flatten()) | {phy.reset, phy.link_up, phy.cd_sys.clk, phy.cd_sys.rst}
    phy.reset.name_override = "phy_reset"
    phy.link_up.name_override = "link_up"
    for direction in ("sink", "source"):
        for signal in getattr(phy, direction).flatten():
            ios.add(signal)
            signal.name_override = direction + "_" + signal.backtrace[-1][0]
    for name in ("eth_tx", "eth_rx", "eth_tx_half", "eth_rx_half"):
        cd = getattr(phy, "cd_" + name)
        cd.clk.name_override = name + "_clk"
        ios.add(cd.clk)
    fragment = phy.get_fragment()
    output = verilog.convert(fragment, ios=ios, name="phy", special_overrides={
        AsyncResetSynchronizer: XilinxAsyncResetSynchronizer,
    })
    destination = destination.resolve()
    destination.with_suffix(".v").write_text(output.main_source)
    for name, contents in output.data_files.items():
        (destination.parent / name).write_text(contents)
    subprocess.run(["yosys", "-Q", "-T", "-q", "-p",
        f'read_verilog {json.dumps(destination.with_suffix(".v").name)}; proc; opt; memory_collect; '
        f'write_json {json.dumps(destination.with_suffix(".json").name)}'],
        cwd=destination.parent, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    netlist = json.loads(destination.with_suffix(".json").read_text())["modules"]["phy"]
    return {"fingerprint": fingerprint(netlist), "cells": len(netlist["cells"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--variant", help="Only names containing this string")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, cls, kwargs in variants():
        if args.variant and args.variant not in name:
            continue
        with redirect_stdout(io.StringIO()):
            try:
                summary[name] = capture(cls, kwargs, args.output_dir / name)
            except ValueError as error:
                summary[name] = {"error": str(error)}
        print(name, summary[name], flush=True)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
