# High-speed packet datapaths

LiteEth can use 128-, 256- or 512-bit UDP/IP/MAC-header streams while keeping the
physical MAC datapath at 64 bits. This requires LiteX's short-header Depacketizer
support. Use the existing stream endpoints, byte enables and width converters;
a separate wide-core hierarchy is not needed.

## Clock and module ownership

`LiteEthUDPIPCore(..., dw=256, with_sys_datapath=False)` puts protocol processing
in `sys`. The MAC converts widths across its TX/RX clock-domain boundaries; CRC,
preamble and padding processing remain at the PHY's supported width. Native
25GBASE-R presents 64 bits at 390.625 MHz. Widening the application stream does
not change the PCS, transceiver or line rate.

The standalone generator keeps this separation. In contrast, LiteX's
`SoC.add_etherbone()` can rename the protocol core's `sys` domain to `eth_rx`
when no software MAC is requested. Check the actual domain mapping before
calculating a bandwidth limit from the board's system clock.

The core owns packet parsing and checksums, the MAC owns framing and width/clock
conversion, the PHY owns line coding and serialization, and the board target
owns pins and physical clock constraints. Follow the existing LiteX module and
endpoint conventions rather than introducing a parallel set of wide interfaces.

## Example

Run:

```sh
python3 -m liteeth.gen examples/udp_baser_25g.yml
```

This example uses a 256-bit core and UDP stream at 200 MHz, a 161.1328125 MHz
GTY reference, and native 25GBASE-R without FEC. Select a matching FPGA and board
clock, constrain the transceiver and pins, and configure the peer accordingly.
The 51.2 Gb/s raw application bus capacity provides headroom; it does not imply
that all packet sizes sustain 25 Gb/s. See [BASE-R capabilities](baser.md).

The stream port buffers complete packets. FIFO depths are in words, while the
MTU and packet-length limit are in bytes. Use `sink_keep`/`source_keep` for
partial final words and assert `sink_last` at packet boundaries. An automatic
length limit rounds down to a full stream word.

## Packet processing

Paths wider than 64 bits use a balanced, registered IPv4 checksum reduction.
The serial checksum remains the default for existing narrower paths. The UDP
packetizer directly controls framing; IP address and length remain latched
until its final output transfer. The UDP receiver captures enclosing IP
metadata on the first input transfer, so a retained payload cannot acquire the
next packet's metadata under backpressure.

Measure the isolated UDP/IP/MAC-header loop with:

```sh
PYTHONPATH=. python3 bench/udp_throughput.py --data-width 128 256 512 --length 18 1472 8972 --packets 4
```

The benchmark includes unicast destination lookup with a deterministic resolved
ARP response, both checksum directions, header insertion/removal and elastic
registers at MAC boundaries. It validates payload, parameters and byte enables.
It excludes PHY timing, wire overhead and unresolved ARP traffic. Results are
steady completion intervals after the first packet, in core clock cycles:

| Width | Payload | Serial checksum | Parallel checksum |
| --- | ---: | ---: | ---: |
| 128 | 18 | 17 | 8 |
| 256 | 18 | 14 | 7 |
| 512 | 18 | 14 | 6 |
| 128 | 1472 | 119 | 103 |
| 256 | 1472 | 72 | 56 |
| 512 | 1472 | 49 | 33 |
| 128 | 8972 | 588 | 572 |
| 256 | 8972 | 307 | 291 |
| 512 | 8972 | 166 | 150 |

The comparison forces the checksum mode on the same packet path. These are
packet-processing measurements, not Ethernet wire throughput. Minimum-size
packets still have a substantial per-packet cost; widening alone is not enough
for 100G minimum-frame line rate.

## Timing and regression checks

Generate the same benchmark for out-of-context timing analysis:

```sh
PYTHONPATH=. python3 bench/udp_throughput.py --data-width 128 256 512 --output-dir build/udp_path
```

For each width, use Vivado's `synth_design -mode out_of_context`, create a clock
on `sys_clk`, then run `opt_design`, `place_design`, `phys_opt_design`,
`route_design` and `report_timing_summary`. Vivado 2024.1 on
`xcku3p-ffva676-2-e`, with a 4 ns clock and four threads, gave these routed
internal setup margins:

| Width | WNS at 250 MHz |
| --- | ---: |
| 128 | +1.285 ns |
| 256 | +1.444 ns |
| 512 | +1.291 ns |

These builds have no input/output delay constraints. They establish internal
register timing only, not board timing or complete 25G PHY timing closure.

`test_wide_datapath.py` covers checksum reference values, reset/enable behavior,
changing packet lengths, stalls, processing rate and generator elaboration.
`test_wide_datapath_rtl.py` exercises generated RTL, including complete MAC/core
loopback with short, standard and jumbo payloads. Its full-core test retains CDC
FIFOs but uses a common clock; it does not validate asynchronous clock ratios.
The packet path uses Icarus; the complete MAC/core uses Verilator 5, which
handles the grouped combinational processes without Icarus scheduling loops.
Existing width-converter, MAC, protocol and frontend tests remain necessary.

## Toward 100G

This work supplies reusable wide packet processing, measurements and tests. It
does not implement a 100G PHY. A subsequent 100G project must select its physical
interface and account for lane distribution/alignment, required FEC, clocking,
MAC framing at the chosen width and device-specific hard-IP integration where
appropriate. Keep these responsibilities separate from the protocol core.
Validate sustained small-packet rate as well as bulk bandwidth before describing
an implementation as line-rate 100G.
