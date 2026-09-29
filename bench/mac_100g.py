#!/usr/bin/env python3
#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Simulate a 100G hardware-MAC interface and a complete UDP/IP core with independent clocks."""

import json
import math
import struct
import argparse
import subprocess
from pathlib import Path

from migen import ClockDomain

from litex.gen import LiteXModule
from litex.gen.fhdl import verilog

from liteeth.core import LiteEthUDPIPCore
from liteeth.mac.axis import LiteEthMACAXIStream

# Packet Helpers ----------------------------------------------------------------------------------

MAC_ADDRESS = 0x10e2d5000000
IP_ADDRESS  = 0xc0a80132
PEER_IP     = 0xc0a80101


def payload(number, length):
    return number.to_bytes(4, "little") + bytes((i + number) & 0xff for i in range(4, length))


def frame(number, length, transmit=False):
    data = payload(number, length)
    source_ip = IP_ADDRESS if transmit else PEER_IP
    target_ip = 0xc0a801ff if transmit else IP_ADDRESS
    ip = struct.pack("!BBHHHBBHII", 0x45, 0, 28 + length, 0, 0, 0x80, 17, 0, source_ip, target_ip)
    total = sum(struct.unpack("!10H", ip))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    ip = ip[:10] + struct.pack("!H", total ^ 0xffff) + ip[12:]
    target_mac = (1 << 48) - 1 if transmit else MAC_ADDRESS
    source_mac = MAC_ADDRESS if transmit else 0x10e2d5000001
    ethernet = target_mac.to_bytes(6, "big") + source_mac.to_bytes(6, "big") + b"\x08\x00"
    return ethernet + ip + struct.pack("!HHHH", 1000 + number, 2000, length + 8, 0) + data


def words(data, width=512, error=False):
    lanes = width//8
    result = []
    for offset in range(0, len(data), lanes):
        part = data[offset:offset + lanes]
        last = offset + lanes >= len(data)
        result.append(int.from_bytes(part, "little") | (((1 << len(part)) - 1) << width) |
            (int(last) << (width + lanes)) | (int(error and last) << (width + lanes + 1)))
    return result


def wire_bits(length):
    # Length includes Ethernet/IP/UDP headers but excludes FCS, as on the CMAC AXI interface.
    return (max(length + 4, 64) + 8 + 12)*8

# DUT ----------------------------------------------------------------------------------------------

class MAC100GCore(LiteXModule):
    def __init__(self, dw=512, sys_clk_freq=250e6, fifo_depth=256):
        self.phy = phy = LiteEthMACAXIStream(rx_fifo_depth=fifo_depth, tx_fifo_depth=fifo_depth)
        self.core = core = LiteEthUDPIPCore(phy, MAC_ADDRESS, IP_ADDRESS, sys_clk_freq,
            dw              = dw,
            eth_mtu         = 9022,
            with_icmp       = False,
            tx_cdc_buffered = True,
            rx_cdc_buffered = True,
        )
        self.port = core.udp.crossbar.get_port(2000, dw=dw)

    def pins(self):
        inputs, outputs = {}, {}
        for prefix, endpoint, fields, sending in [
            ("app_tx", self.port.sink, ["data", "be", "last", "length", "src_port", "dst_port", "ip_address"], True),
            ("app_rx", self.port.source, ["data", "be", "last", "length", "src_port", "dst_port", "ip_address", "error"], False),
            ("mac_rx", self.phy.rx, ["data", "keep", "last", "user"], True),
            ("mac_tx", self.phy.tx, ["data", "keep", "last", "user"], False),
        ]:
            for field in ["valid"] + fields:
                (inputs if sending else outputs)[prefix + "_" + field] = getattr(endpoint, field)
            if prefix != "mac_rx":
                (outputs if sending else inputs)[prefix + "_ready"] = endpoint.ready
        outputs.update(
            rx_packets    = self.phy.rx_packets,
            rx_drops      = self.phy.rx_drops,
            rx_bad_frames = self.phy.rx_bad_frames,
        )
        return inputs, outputs

# Generation / Simulation -------------------------------------------------------------------------

def generate(path, lengths, dw=512, sys_clk_freq=250e6, rx_clk_freq=322.265625e6,
    tx_clk_freq=322.265625e6, rate=100e9, fifo_depth=256, stall_cycles=0, error_every=0,
    with_reset=False):
    if len(lengths) < 2 or any(n < 18 or n > 8972 for n in lengths):
        raise ValueError("Use at least two packets with UDP payloads from 18 to 8972 bytes.")
    if dw not in [128, 256, 512] or not 0 < rate <= 100e9:
        raise ValueError("Use a 128/256/512-bit application and a rate in (0, 100G].")
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    dut = MAC100GCore(dw, sys_clk_freq, fifo_depth)
    inputs, outputs = dut.pins()
    ios          = set()
    ports        = []
    declarations = []
    clocks       = []
    for name, freq in [("sys", sys_clk_freq), ("eth_rx", rx_clk_freq), ("eth_tx", tx_clk_freq)]:
        cd = ClockDomain(name)
        setattr(dut.clock_domains, "cd_" + name, cd)
        ios.update([cd.clk, cd.rst])
        ports += [f".{name}_clk({name}_clk)", f".{name}_rst(rst)"]
        clocks.append(f"reg {name}_clk = 0; always #{5e11/freq:.6f} {name}_clk = ~{name}_clk;")
    for pins, kind in [(inputs, "reg"), (outputs, "wire")]:
        for name, signal in pins.items():
            signal.name_override = name
            ios.add(signal)
            ports.append(f".{name}({name})")
            declarations.append(f"{kind} [{len(signal)-1}:0] {name}" + (" = 0;" if kind == "reg" else ";"))
    verilog.convert(dut, ios=ios, name="dut", comb_cycle_policy="error").write(str(path/"dut.v"))
    incoming, outgoing, application, schedule = [], [], [], []
    start_ps = 0
    for n, length in enumerate(lengths):
        packet = frame(n, length)
        encoded = words(packet, error=error_every > 0 and (n + 1) % error_every == 0)
        start_cycle = math.ceil(start_ps*rx_clk_freq/1e12)
        schedule += list(range(start_cycle, start_cycle + len(encoded)))
        incoming += encoded
        outgoing += words(frame(n, length, transmit=True))
        application += words(payload(n, length), dw)
        start_ps += wire_bits(len(packet))*1e12/rate
    for name, data in [("rx_words", incoming), ("tx_words", outgoing), ("app_words", application),
                       ("rx_schedule", schedule), ("lengths", lengths)]:
        (path/(name + ".hex")).write_text("\n".join(f"{value:x}" for value in data) + "\n")
    template = (Path(__file__).parent/"sim"/"mac_100g.sv").read_text()
    config = dict(
        DW           = dw,
        LANES        = dw//8,
        PACKETS      = len(lengths),
        RX_WORDS     = len(incoming),
        TX_WORDS     = len(outgoing),
        APP_WORDS    = len(application),
        STALL_CYCLES = stall_cycles,
        ERROR_EVERY  = error_every,
        RESET_TEST   = int(with_reset),
        CLOCKS       = "\n".join(clocks),
        DECLARATIONS = "\n".join(declarations),
        PORTS        = ", ".join(ports),
        DRAIN_CYCLES = max(2000, len(incoming)*8),
        SYS_FREQ     = sys_clk_freq,
        RATE         = rate,
    )
    for key, value in config.items():
        template = template.replace("@" + key + "@", str(value))
    (path/"tb.sv").write_text(template)
    return path


def simulate(path):
    path = Path(path).resolve()
    command = [
        "verilator", "--binary", "--timing", "--top-module", "tb",
        "-Wno-fatal", "-j", "2", "dut.v", "tb.sv",
    ]
    with (path/"build.log").open("w") as log:
        subprocess.run(command, cwd=path, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    result = subprocess.run([str(path/"obj_dir"/"Vtb")], cwd=path, capture_output=True, text=True, timeout=60)
    (path/"simulation.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError("Simulation produced no measurements")

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Output.
    parser.add_argument("--output-dir",    default="build/mac_100g")
    parser.add_argument("--generate-only", action="store_true")

    # Traffic.
    parser.add_argument("--profile",       choices=["minimum", "standard", "jumbo", "mixed"], default="mixed")
    parser.add_argument("--packets",       type=int,   default=128)
    parser.add_argument("--rate",          type=float, default=100e9)

    # Data path.
    parser.add_argument("--data-width",    type=int,   default=512, choices=[128, 256, 512])
    parser.add_argument("--sys-clk-freq",  type=float, default=250e6)
    parser.add_argument("--rx-clk-freq",   type=float, default=322.265625e6)
    parser.add_argument("--tx-clk-freq",   type=float, default=322.265625e6)
    parser.add_argument("--fifo-depth",    type=int,   default=256)

    # Fault injection.
    parser.add_argument("--stall-cycles",  type=int, default=0)
    parser.add_argument("--error-every",   type=int, default=0)
    parser.add_argument("--reset",         action="store_true")
    args = parser.parse_args()
    sizes = {
        "minimum"  : [18],
        "standard" : [1472],
        "jumbo"    : [8972],
        "mixed"    : [18, 65, 1472, 8972, 33],
    }
    profile = sizes[args.profile]
    lengths = [profile[i % len(profile)] for i in range(args.packets)]
    path = generate(args.output_dir, lengths,
        dw           = args.data_width,
        sys_clk_freq = args.sys_clk_freq,
        rx_clk_freq  = args.rx_clk_freq,
        tx_clk_freq  = args.tx_clk_freq,
        rate         = args.rate,
        fifo_depth   = args.fifo_depth,
        stall_cycles = args.stall_cycles,
        error_every  = args.error_every,
        with_reset   = args.reset,
    )
    if not args.generate_only:
        print(json.dumps(simulate(path), sort_keys=True))


if __name__ == "__main__":
    main()
