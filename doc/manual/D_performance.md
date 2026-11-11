# Performance and resources {#performance}

This appendix gathers what limits the throughput of a LiteEth design and the figures recorded for
it. It starts from the theoretical limits that the Ethernet standard sets, then the capacity of
the datapaths, then the measurements that the repository and public projects record. Use it to
size a design before building it.

## What the wire allows {#perf-wire}

Every frame on the wire carries a fixed overhead: 8 bytes of preamble and SFD before it, 12 bytes
of inter-frame gap after it, a 14-byte header and a 4-byte FCS. A UDP packet adds 20 bytes of
IPv4 header and 8 bytes of UDP header. The useful share of the line therefore depends on the
frame size. [Ethernet in a nutshell](#ethernet-primer) explains these fields.

The table below is computed from these sizes, not measured. It gives the UDP payload of three
frame sizes: the minimum frame of 64 bytes (18 bytes of UDP payload), the standard maximum of 1518
bytes (1472 bytes) and a jumbo frame of 9018 bytes (8972 bytes, for an MTU of 9000).

| Frame (bytes) | UDP payload | Wire bytes | UDP efficiency | MAC payload efficiency |
|----------------|-------------|------------|----------------|------------------|
| 64 | 18 | 84 | 21.4 % | 54.8 % |
| 1518 | 1472 | 1538 | 95.7 % | 97.5 % |
| 9018 | 8972 | 9038 | 99.3 % | 99.6 % |

Both efficiencies are payload bytes over wire bytes. The UDP efficiency counts the UDP payload;
the MAC payload efficiency counts the payload of the Ethernet frame (46, 1500 and 9000 bytes: IP
header, UDP header and data), as a user of raw frames sees it. Applied to each line rate, the UDP
efficiency gives the largest UDP throughput a link can carry, and the frame size gives the
largest frame rate:

| Line rate | Frames/s at 64 bytes | Frames/s at 1518 bytes | UDP at 64 B | UDP at 1518 B | UDP at 9018 B |
|--------------|---------------------|-----------------------|-------------|---------------|---------------|
| 10 Mbps | 14 881 | 813 | 0.27 MB/s | 1.20 MB/s | 1.24 MB/s |
| 100 Mbps | 148 810 | 8 127 | 2.68 MB/s | 12.0 MB/s | 12.4 MB/s |
| 1 Gbps | 1.488 M | 81 274 | 26.8 MB/s | 119.6 MB/s | 124.1 MB/s |
| 2.5 Gbps | 3.72 M | 203 186 | 67.0 MB/s | 299.1 MB/s | 310.2 MB/s |
| 5 Gbps | 7.44 M | 406 372 | 133.9 MB/s | 598.2 MB/s | 620.4 MB/s |
| 10 Gbps | 14.88 M | 812 744 | 267.9 MB/s | 1196 MB/s | 1241 MB/s |
| 25 Gbps | 37.2 M | 2.03 M | 669.6 MB/s | 2991 MB/s | 3102 MB/s |
| 100 Gbps | 148.8 M | 8.13 M | 2679 MB/s | 11 963 MB/s | 12 409 MB/s |

MB/s is 10^6 bytes per second. A design that sends short packets is bound by the packet rate,
not the bit rate: at 10 Gbps, one minimum frame every 67.2 ns.

## Datapath capacity {#perf-datapath}

Inside the FPGA, a datapath carries at most its width times its clock, one word per cycle. Each
stage of a design must reach the line rate, or the slowest stage sets the limit. The table below
computes this capacity for the widths and clocks of the examples and targets of
[Examples and targets](#examples).

| Design | Width | Clock | Capacity | Line rate |
|---------------------------------------|---------|---------------|-------------|------------|
| 8-bit core of a 1G target | 8 | 125 MHz | 1 Gbps | 1 Gbps |
| `udp_a7_gtp_sgmii` core | 32 | 25 MHz | 0.8 Gbps | 1 Gbps |
| Artix-7 5G targets | 32 | 100 MHz | 3.2 Gbps | 5 Gbps |
| `udp_baser` core | 64 | 156.25 MHz | 10 Gbps | 10 Gbps |
| `alibaba_10g` at 25G, Etherbone side | 64 | 200 MHz | 12.8 Gbps | 25 Gbps |
| `udp_baser_25g` core | 256 | 200 MHz | 51.2 Gbps | 25 Gbps |
| `mac_100g` default | 512 | 250 MHz | 128 Gbps | 100 Gbps |

A capacity below the line rate is a choice of these designs: the core of `udp_a7_gtp_sgmii` runs
at 25 MHz, the Artix-7 5G targets are bring-up designs. In `alibaba_10g` at 25G the 64-bit core
runs in the 390.625 MHz clock of the PHY; only the Etherbone, in `sys`, is capped at 12.8 Gbps. A capacity above it gives headroom for the
idle cycles of the core, but does not guarantee the line rate for short packets: the per-packet
work of the header path counts as well.

## Header path {#perf-header-path}

`bench/udp_throughput.py` measures the UDP, IP and MAC header path alone, in cycles per packet
once the flow is steady ([Simulation and verification](#simulation)). ARP is answered at once and
the PHY and the wire are left out, so these are packet processing figures, not Ethernet
throughput. The repository records the results below, comparing the serial IPv4 checksum with the
parallel one used above 64 bits.

| Width | Payload (bytes) | Serial checksum | Parallel checksum |
|---------|-----------------|-----------------|-------------------|
| 128 | 18 | 17 | 8 |
| 256 | 18 | 14 | 7 |
| 512 | 18 | 14 | 6 |
| 128 | 1472 | 119 | 103 |
| 256 | 1472 | 72 | 56 |
| 512 | 1472 | 49 | 33 |
| 128 | 8972 | 588 | 572 |
| 256 | 8972 | 307 | 291 |
| 512 | 8972 | 166 | 150 |

The table was recorded before the packet-start overlap, which the code of this revision has: the
repository also records that overlapping the start of each packet with the end of the previous
one brings the 18-byte intervals from 8, 7 and 6 cycles down to 6, 5 and 4 cycles at 128, 256 and
512 bits. The parallel column is therefore an upper bound for this revision; run
`bench/udp_throughput.py` for its own figures. At 250 MHz, 4 cycles per packet give 62.5 million packets per second, below the
148.8 million minimum frames per second of a 100 Gbps line: widening alone does not reach 100G at
minimum frames.

The same header path was built out of context in Vivado 2024.1 on an `xcku3p-ffva676-2-e`, with a
4 ns clock (250 MHz). The routed setup slack recorded is below. These builds have no input or output
delay constraints: they show the internal timing of the path, not the timing of a board or of a
25G PHY.

| Width | WNS at 250 MHz |
|---------|----------------|
| 128 | +1.468 ns |
| 256 | +1.305 ns |
| 512 | +0.985 ns |

## 100G adapter path {#perf-100g}

The 100G path is in development ([BASE-R PHYs](#baser-phys)). The repository records one routed
timing build of the AXI-Stream adapter, the clock-domain crossings and the UDP path, made with
`bench/mac_100g_timing.py` in Vivado 2024.1 on the default `xcvu3p-ffvc1517-2-e`.

| Item | Value |
|------------------------|---------------------------------------------|
| Clocks | 250 MHz `sys`, 322.265625 MHz MAC |
| Setup slack | +0.067 ns |
| Hold slack | +0.043 ns |
| LUTs | 4463 |
| Registers | 8576 |
| Block RAM tiles | 17.5 |

This is internal register timing: the CMAC, the transceiver routing and the board are excluded,
and the margin is small. The Verilator traffic bench of the same path shows the minimum-frame
overload: its regression test expects drops at 18-byte payloads and none at 1472 bytes.

## Etherbone {#perf-etherbone}

Etherbone carries one record per frame. Write records get no reply, so the host sends them
without waiting; the host waits for the reply of each read before it sends the next one, so reads
are bound by the round trip.

The table below is a public measurement from the LiteX-M2SDR documentation (`doc/sata-validation.md`,
"Etherbone host-buffer burst benchmark"). It was taken with `m2sdr_sata diag etherbone-bench
--iterations 3` on a LiteX-M2SDR on an Acorn Baseboard Mini, over a 1000BASE-X SFP link. That
design uses the LiteX-M2SDR variant of the Etherbone frontend, with a `buffer_depth` of 160. The
rates are in MiB/s (2^20 bytes per second), as the tool prints them; Bytes is the total of the 3
iterations (3 x 4 bytes per word), not the size of one burst: a 128-word burst is 512 bytes.

```text
| Words | Bytes | Write MiB/s | Read MiB/s |
| ----: | ----: | ----------: | ---------: |
| 1 | 12 | 0.880 | 0.107 |
| 2 | 24 | 1.272 | 0.214 |
| 4 | 48 | 3.270 | 0.428 |
| 8 | 96 | 6.539 | 0.864 |
| 16 | 192 | 14.085 | 1.695 |
| 32 | 384 | 26.158 | 2.977 |
| 64 | 768 | 56.340 | 5.051 |
| 128 | 1536 | 104.632 | 7.710 |
```

The write column measures how fast the host issues the records, not what reaches the FPGA, and
it is not a wire rate of LiteEth. A 128-word write record takes 594 bytes on the wire (512 bytes
of data, the Etherbone, UDP, IP and Ethernet headers, the FCS, the preamble and the gap), so a
1 Gbps link carries at most about 102.7 MiB/s of such writes; 104.632 MiB/s is above that bound.
The read column shows the round-trip limit: 7.710 MiB/s at 128 words.

The same documentation records, in another run with bursts of 128 words to its staging RAM only
(`doc/notes/sata-bandwidth-investigation.md`), the figures below. The write figure is also a
host-side issue rate, above the 1 Gbps bound.

```text
| Etherbone burst write (staging RAM only) | `diag etherbone-bench`, 128 words | 139.5 MiB/s |
| Etherbone burst read (staging RAM only) | `diag etherbone-bench`, 128 words | 7.0 MiB/s |
```

`bench/test_etherbone.py --speed` measures the same kind of figure on any Etherbone target whose
`buffer_depth` holds 255 words, with 16 bursts of 255 words, and prints it in KiB/s; its writes
get no reply either. The repository records no result of it.

## UDP streaming {#perf-udp}

The LiteX-M2SDR project also records a UDP stream through LiteEth on the same board and link
(`doc/debugging-guide.md`): samples sent by the host, looped back by the FPGA and checked on
return, at 1.92 MS/s. It is a functional check at a low rate, not a throughput limit.

```text
[> FPGA PHY loopback test:
Device      : eth:192.168.1.50:1234
Sample rate : 1920000 S/s
Duration    : 4 s
Mode        : pace=rx, window=32 buffers
Path        : host TX -> FPGA RFIC data loopback -> host RX
RX 0.12 Gbps | checked 1811 buffers | errors 0
PASS: checked 7434 buffers, 0 errors, RX 0.12 Gbps
[...]
```

The repository records no hardware throughput of the UDP streamer or of the UDP user ports at
any rate. To measure one, send from a user port on the FPGA and count on the host, and compare the
result with the tables of [What the wire allows](#perf-wire).

## Resources {#perf-resources}

The repository records no FPGA resource figures for the MAC, the core or the PHYs, except the
100G adapter path above. The resources depend on the width, the FIFO depths, the frontends and
the PHY. To size a design, build the closest target or example with `--build` and read the
utilization report of the toolchain.

Where to go next: [Debugging](#debugging) when a design stays below these figures;
[Core architecture](#architecture) for the clock domains and widths that set them.
