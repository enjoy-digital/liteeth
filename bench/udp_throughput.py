#!/usr/bin/env python3
#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Measure the UDP/IP/MAC header path in cycles, without a PHY or Ethernet wire overhead."""

import argparse
import json
import random
from pathlib import Path

from migen import *
from litex.gen import LiteXModule
from litex.soc.interconnect import stream

from liteeth.common import *
from liteeth.core.ip import LiteEthIPTX, LiteEthIPRX
from liteeth.core.udp import LiteEthUDPTX, LiteEthUDPRX
from liteeth.mac.common import LiteEthMACPacketizer, LiteEthMACDepacketizer


class ARPTable:
    def __init__(self):
        self.request  = stream.Endpoint(arp_table_request_layout)
        self.response = stream.Endpoint(arp_table_response_layout)


class UDPPath(LiteXModule):
    def __init__(self, dw):
        address = 0xc0a80132
        mac     = 0x10e2d5000000
        # A resolved destination isolates packet processing from ARP traffic on the wire.
        table = ARPTable()
        pending = Signal()
        self.comb += [
            table.request.ready.eq(~pending),
            table.response.valid.eq(pending),
            table.response.mac_address.eq(mac),
        ]
        self.sync += If(table.request.valid & table.request.ready, pending.eq(1)).Elif(
            table.response.ready, pending.eq(0))
        self.udp_tx = LiteEthUDPTX(address, dw)
        self.ip_tx  = LiteEthIPTX(mac, address, table, dw)
        self.mac_tx = LiteEthMACPacketizer(dw)
        self.mac_rx = LiteEthMACDepacketizer(dw)
        self.ip_rx  = LiteEthIPRX(mac, address, dw=dw)
        self.udp_rx = LiteEthUDPRX(address, dw)
        # Match the register boundaries of the high-rate MAC crossbar and replace the physical
        # link with an elastic register. The benchmark measures packet processing, not wire rate.
        self.tx_buffer = stream.Buffer(eth_mac_description(dw), pipe_valid=True, pipe_ready=True)
        self.rx_buffer = stream.Buffer(eth_mac_description(dw), pipe_valid=True, pipe_ready=True)
        self.link_buffer = stream.Buffer(eth_phy_description(dw), pipe_valid=True, pipe_ready=True)
        self.pipeline = stream.Pipeline(self.udp_tx, self.ip_tx, self.tx_buffer, self.mac_tx,
            self.link_buffer, self.mac_rx, self.rx_buffer, self.ip_rx, self.udp_rx)
        self.sink   = self.udp_tx.sink
        self.source = self.udp_rx.source


def measure(dw=128, length=1472, packets=32, stalls=False):
    lengths = length if isinstance(length, list) else [length]*packets
    if min(lengths) < 1 or len(lengths) < 2:
        raise ValueError("Use a positive payload length and at least two packets.")
    packets = len(lengths)
    dut     = UDPPath(dw)
    lanes   = dw//8
    payloads = [bytes((i + n) % 256 for i in range(size)) for n, size in enumerate(lengths)]
    completions = []
    errors = []

    def producer():
        for n, data in enumerate(payloads):
            length = len(data)
            yield dut.sink.ip_address.eq(0xc0a80132)
            yield dut.sink.src_port.eq(1000 + n)
            yield dut.sink.dst_port.eq(2000)
            yield dut.sink.length.eq(length)
            for offset in range(0, length, lanes):
                word = data[offset:offset + lanes]
                yield dut.sink.valid.eq(1)
                yield dut.sink.data.eq(int.from_bytes(word, "little"))
                yield dut.sink.be.eq((1 << len(word)) - 1)
                yield dut.sink.last.eq(offset + lanes >= length)
                yield
                for _ in range(1000):
                    if (yield dut.sink.ready):
                        break
                    yield
                else:
                    raise AssertionError("UDP input stalled")
            yield dut.sink.valid.eq(0)

    def consumer():
        prng = random.Random(42)
        packet = bytearray()
        stalled = None
        for cycle in range(sum(size//lanes + 100 for size in lengths)*8):
            yield dut.source.ready.eq(prng.randrange(4) != 0 if stalls else 1)
            yield
            valid = (yield dut.source.valid)
            item = ((yield dut.source.data), (yield dut.source.be), (yield dut.source.last),
                (yield dut.source.src_port), (yield dut.source.dst_port),
                (yield dut.source.length), (yield dut.source.ip_address), (yield dut.source.error))
            if stalled is not None and (not valid or item != stalled):
                raise AssertionError(f"UDP output changed under backpressure: {stalled} -> {item}, valid={valid}")
            stalled = item if valid and not (yield dut.source.ready) else None
            if valid and (yield dut.source.ready):
                data, mask, last, src, dst, size, address, error = item
                n = len(completions)
                if (src, dst, size, address) != (1000 + n, 2000, lengths[n], 0xc0a80132):
                    raise AssertionError(f"Incorrect UDP metadata: {item[3:7]}")
                packet.extend((data >> (8*i)) & 0xff for i in range(lanes) if mask & (1 << i))
                errors.append(error & mask)
                if last:
                    if packet != payloads[n]:
                        raise AssertionError(f"Incorrect UDP payload {n}: {len(packet)} bytes")
                    packet.clear()
                    completions.append(cycle)
                    if len(completions) == packets:
                        if any(errors):
                            raise AssertionError("UDP packet flagged as erroneous")
                        return
        raise AssertionError(f"UDP output timed out after {len(completions)} packets")

    run_simulation(dut, [producer(), consumer()])
    cycles = (completions[-1] - completions[0])/(packets - 1)
    return dict(data_width=dw, payload_bytes=length, packets=packets,
        cycles_per_packet=cycles, payload_bits_per_cycle=8*sum(lengths[1:])/(packets - 1)/cycles)


def generate(dw, output_dir):
    from litex.gen.fhdl import verilog

    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    dut = UDPPath(dw)
    dut.clock_domains.cd_sys = ClockDomain("sys")
    ios = set(dut.sink.flatten() + dut.source.flatten() + [dut.cd_sys.clk, dut.cd_sys.rst])
    verilog.convert(dut, ios=ios, name=f"udp_path_{dw}", comb_cycle_policy="error").write(
        str(path/f"udp_path_{dw}.v"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-width", type=int, nargs="+", default=[64, 128, 256, 512])
    parser.add_argument("--length", type=int, nargs="+", default=[18, 64, 65, 1472, 8972])
    parser.add_argument("--packets", type=int, default=32)
    parser.add_argument("--output-dir", help="Generate Verilog for timing analysis instead of simulating.")
    args = parser.parse_args()
    for width in args.data_width:
        if args.output_dir:
            generate(width, args.output_dir)
            continue
        for length in args.length:
            print(json.dumps(measure(width, length, args.packets)), flush=True)


if __name__ == "__main__":
    main()
