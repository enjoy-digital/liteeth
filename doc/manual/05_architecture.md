```{=latex}
\part{The core}
```

```{=html}
<h1 class="part">Part II. The core</h1>
```

# Core architecture {#architecture}

This chapter describes how the core is organised: its layers, the stream contract they share, the
crossbars that connect them, the clock domains and data widths, and where packets are buffered or
dropped.

Read it before the chapters on each layer, or before adding your own block to the core. The
protocols themselves are explained in [ARP, IP, UDP and Etherbone](#network-primer).

## At a glance {#arch-glance}

| Item | Value |
|------------------------|------------------------------------------------------------------|
| Top-level classes | `LiteEthUDPIPCore`, `LiteEthIPCore` (no UDP), `LiteEthMAC` |
| Interfaces | LiteX ready/valid streams, one packet per `last` |
| Byte qualifier | `be`, one bit per byte lane, on every beat |
| Errors | `error`, one bit per byte lane |
| Crossbars | MAC by EtherType, IP by protocol, UDP by destination port |
| Clock domains | `eth_tx` and `eth_rx` of the PHY, `sys` of the core, one per UDP user port |
| Framing widths | 8, 32 or 64 bits |
| Core widths | 8 to 512 bits |
| Buffering | Optional store-and-forward FIFOs at the PHY and on UDP ports |

## Layers {#arch-layers}

The core is a stack of layers, each a small module with a transmit and a receive half.

User logic reaches the network through a frontend or a UDP port; UDP and ICMP sit on IPv4; IPv4 and
ARP sit on the MAC; the MAC drives a PHY, which drives an external PHY chip or an SFP module. The
figure below shows these layers.

![Core architecture. Follow the cyan path of a UDP packet: UDP adds its header, IPv4 adds its header
after an ARP lookup, the MAC frames it for the PHY.](arch_blocks.svg)

Each layer owns one job, so that a change in one layer does not ripple through the others. The table
below lists who owns what.

| Owner | Responsibility |
|-------------------|-------------------------------------------------------------------------|
| Core layers | Header insertion and parsing, checksums, ARP resolution, port dispatch |
| MAC | Framing (padding, FCS, preamble, gap), width and clock conversion, frame buffering |
| PHY | Line coding, serialization, the `eth_tx` and `eth_rx` clocks |
| LiteICLink | Transceiver PLLs, dynamic reconfiguration and initialization helpers |
| Target | Pins, reference clocks, timing constraints |

## Streams and the packet contract {#arch-streams}

Every layer exchanges packets on LiteX streams. A beat transfers when `valid` and `ready` are both
high. While a beat waits, the producer holds its data, byte enables, errors, `last` and parameters
stable. Parameters (addresses, ports, lengths) describe the whole packet and stay stable until its
last beat has transferred.

### Byte enables {#arch-be}

The `be` field marks the valid bytes of a beat, one bit per byte lane, bit 0 for the lowest eight
data bits. Intermediate beats enable every lane.

The last beat has a nonzero mask that is contiguous from bit 0. An 8-bit stream drives `be` at 1 on
every beat. The figure below shows a 20-byte packet on a 64-bit stream.

![A 20-byte packet on a 64-bit stream. Look at the last beat: four bytes are valid, so `be` is
`0x0f` and `last` is set; the shaded lanes carry no data.](arch_stream.svg)

The rules leave no room for interpretation. A zero mask never means "all bytes valid", as it does
with some older interfaces, and a sparse mask or a partial intermediate beat must be compacted
before it enters the core.

Within these rules, `be` maps directly to the AXI-Stream `tkeep` signal. The mask follows the data
through FIFOs, clock-domain crossings, width converters, header insertion and removal, padding and
CRC.

### Errors {#arch-errors}

The `error` field also has one bit per byte lane, and consumers only look at the lanes that `be`
enables. An error on the last beat can invalidate the whole packet, including bytes that were
delivered earlier.

A block that commits something irreversible, such as a bus write, must therefore wait for the end of
the packet; a FIFO alone does not check errors.

The MAC finds a bad FCS only at the end of a frame. On a path without buffering, an upper layer may
already have passed on a shorter payload by then and cannot take it back. Applications that need
whole-frame integrity buffer and check at their own boundary, as the Etherbone and ICMP echo
blocks do.

### Lengths {#arch-lengths}

Native packets carry at least one byte: the core has no zero-length packet. Each endpoint gives its
length in its own unit, as the table below lists. The UDP and ICMP receive paths drop empty
payloads, and their transmit users must give a nonempty payload with a matching length.

| Endpoint | `length` counts |
|---------------------------------|---------------------------------------------------------|
| MAC and PHY streams | No field: the bytes enabled by `be` up to `last` |
| IPv4 header `total_length` | The IPv4 header and its payload |
| IPv4 user port `length` | The IPv4 payload, transport header included |
| UDP header `length` | The 8-byte UDP header and the application payload |
| UDP user port `length` | The application payload |
| ICMP user port `length` | The payload after the 8-byte ICMP header |
| Etherbone packet `length` | The bytes after the 8-byte Etherbone packet header |
| DHCP UDP `length` | The whole BOOTP/DHCP message |

## Crossbars {#arch-crossbars}

Three crossbars let several users share a layer. The MAC crossbar dispatches received frames by
EtherType, the IP crossbar by IP protocol and the UDP crossbar by destination port. The figure below
shows the three levels and their usual users.

![Crossbars. Each layer takes one port of the crossbar under it: ARP and IPv4 on the MAC crossbar,
ICMP and UDP on the IP crossbar, each UDP user on the UDP crossbar.](arch_crossbars.svg)

A user asks for its port with `get_port()`, giving the value it owns: an EtherType, a protocol
number or a UDP port. Asking twice for the same value raises an error at elaboration.

On transmit, a round-robin arbiter grants one whole packet at a time. On receive, a dispatcher
routes each packet to the port whose value matches, and drains packets that match no port.

The UDP crossbar does more than dispatch. Its `get_port()` takes a data width and a clock domain
and inserts the converter and the clock-domain crossing the user needs; see
[UDP user ports](#stack-udp-ports).

## Clock domains {#arch-clocks}

The core uses three kinds of clock domains, as the figure below shows. The PHY provides `eth_tx` and
`eth_rx`. The MAC crosses them to the `sys` domain of the core, where ARP, IP, UDP and the crossbars
run. Each UDP user port can live in its own domain, with its own crossing.

![Clock domains. Look at the two crossings: the MAC CDC between the PHY clocks and the core, and
a port CDC between the core and each user port that asks for another clock
domain.](arch_clocks.svg)

The MAC crossing is a stream asynchronous FIFO, 32 words deep by default, with a reset common to
both sides (`LiteEthMACCDC`).

The two domains are reset independently: a SoC reboot resets `sys`, a PHY restart or an
autonegotiation resets the PHY side. Either reset also resets the other side of the FIFO, so that no
stale word is replayed and the pointers stay coherent.

The MAC framing stages (padding, CRC and preamble) run in the PHY domains at the PHY width by
default. With `with_sys_datapath`, they run in `sys` at the core width instead, and the crossing
moves to the PHY side of them. The inter-frame gap and the transmit packet FIFO always run in
`eth_tx`, next to the PHY.

The `sys` of the core is a name, not a fixed clock. LiteX renames it when it builds the core, so
check the actual mapping before computing a bandwidth from the board's system clock. The table
below lists what `add_etherbone()` does for each data width.

| `data_width` | Framing path | Core and crossbars | UDP ports in the SoC `sys` |
|-------------------|--------------------|---------------------------|----------------------------|
| 8 | `eth_tx`, `eth_rx` | PHY RX clock (`eth_rx`) | `cd="etherbone"` |
| 32 | `sys` | SoC `sys` | `cd="etherbone"` (`cd="sys"` also works) |
| 64 | `eth_tx`, `eth_rx` | PHY RX clock (`eth_rx`) | `cd="etherbone"` |

At 8 and 64 bits the whole core runs in the PHY receive clock, which avoids a crossing on the
receive path. LiteX also adds a clock domain named after the Etherbone instance (`etherbone` by
default), a copy of the SoC `sys` clock, at every width; at 8 and 64 bits it is the only name for
the SoC clock inside the core.

User ports that live in the SoC `sys` ask for this domain by name. With `with_ethmac`, the core
stays in the SoC `sys` at every width.

## Data widths {#arch-widths}

The MAC converts between the PHY width and the core width. The PHY width is set by the PHY class
(`phy.dw`): 8 bits for MII, RMII, GMII, RGMII and the 8b/10b PHYs, 32 bits for some transceiver
PHYs, 64 bits for XGMII and BASE-R. The core width is the `dw` argument of the core and of the MAC.

| Stage | Supported widths |
|---------------------------------------|-----------------------------------------------|
| CRC inserter and checker | 8, 32, 64 |
| Preamble inserter and checker | 8, 16, 32, 64 |
| Padding inserter | 8 to 512, powers of 2 |
| SRAM writer and reader (software MAC) | 8, 16, 32, 64 |
| Core layers | 8 to 512 |
| Etherbone | 32 internally, converted at its UDP port |

The framing path therefore runs at 8, 32 or 64 bits. With `with_sys_datapath`, that width is the
core width; without it, it is the PHY width, and the core can be wider. With `with_sys_datapath`,
the core width must therefore be 8, 32 or 64: a 16-bit core only works with the framing in the PHY
domains. The generator sets `with_sys_datapath` for a `data_width` of 16, so a 16-bit generator
configuration does not build ([Configuration keys](#integ-gen-keys)).

A 256-bit core on a 25G PHY keeps the MAC framing at 64 bits and converts the width in the MAC
crossing. The IPv4 checksum switches to a parallel form above 64 bits; see [Wide
datapaths](#stack-wide).

Widening the core does not change the PHY, the transceiver or the line rate. It only gives the
protocol layers more bandwidth per `sys` cycle, which matters for small packets whose headers cost
a fixed number of cycles.

## Buffering and drops {#arch-buffering}

A PHY cannot pause in the middle of a frame, in either direction. On transmit, the MAC must feed it
every cycle from the first byte to the last; on receive, it must accept every cycle. When the core
side is slower than the line, whole-frame buffers next to the PHY absorb the difference. The MAC
calls them store-and-forward FIFOs.

| Direction | Buffer | Behaviour |
|-------------|----------------------------|------------------------------------------------|
| TX | `PacketFIFO` in `eth_tx` | Holds each formatted frame whole before starting it; backpressures |
| RX | `PacketDropFIFO` in `eth_rx` | Stores frames whole; drops a frame that does not fit; never backpressures |

The receive FIFO keeps its input ready at all times. A frame that overflows it, or that is discarded
on purpose, is rewound and never appears at the output; the FIFO pulses `drop` once at the end of
that frame.

Each FIFO holds one frame of the configured MTU, preamble and FCS included, rounded up to a power of
two words.

`with_store_and_forward` selects the buffers: `True`, `False` or `"auto"`, the default. In `"auto"`,
the MAC adds them when the PHY width times its clock reaches 2.5 Gbps, or when the core width times
the `sys` clock is below the rate of a PHY of 1 Gbps or more.

A PHY class can force the choice with a `with_store_and_forward` attribute. This is a heuristic, not
a proof that the transmit path never underruns: choose explicit buffering when the traffic needs it.

The transmit FIFO only holds frames up to the configured MTU; a longer frame stalls it. The receive
buffers absorb finite stalls and drop whole frames beyond that, so they never provide unlimited
lossless buffering. The UDP ports add their own buffers when they are narrower or slower than the
core; see [UDP user ports](#stack-udp-ports).

## Pipelining {#arch-pipelining}

Fast PHY clocks leave little logic per cycle. When the PHY receive clock is above 200 MHz, the MAC
adds register stages: after the CRC checker, before the receive drop FIFO, after the MAC header
parser, on each port of the MAC crossbar, and before the transmit packet FIFO.

The threshold is a rough guess: 10GBASE-R at 156.25 MHz stays below it, 25GBASE-R at 390.625 MHz is
above it.

The IP layer always registers its receive output, which breaks the long combinational paths through
the stack when the core runs in `sys`. Like the store-and-forward choice, the pipelining choice is
a default for timing, not a guarantee: run timing analysis on the target device.

## In the code {#arch-code}

| File | Main classes | Role |
|------------------------------------------|----------------------------------|------------------------|
| `liteeth/core/__init__.py` | `LiteEthUDPIPCore`, `LiteEthIPCore` | Core top levels |
| `liteeth/common.py` | stream descriptions, headers | Layouts, constants, heuristics |
| `liteeth/crossbar.py` | `LiteEthCrossbar` | Generic arbiter and dispatcher |
| `liteeth/fifo.py` | `PacketDropFIFO` | Whole-packet store with drop |
| `liteeth/mac/core.py` | `LiteEthMACCDC`, `LiteEthMACCore` | Clock crossing, framing paths |

Where to go next: [MAC](#mac) for the framing paths, [ARP, IP, ICMP, UDP, DHCP and
IGMP](#ip-stack) for the protocol layers, [Integration](#integration) to add the core to a design.
