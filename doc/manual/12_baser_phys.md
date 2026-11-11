# 5G, 10G and 25G BASE-R, and 100G {#baser-phys}

The BASE-R PHYs carry Ethernet at 5, 10 and 25 Gbps over one transceiver lane, with the 64b/66b
line coding done in the FPGA. A native LiteEth PCS sits between an XGMII adapter and a device
PMA, so the same code serves Kintex-7, Artix-7, UltraScale+ and Gowin GW5 transceivers.

This chapter describes the PCS, the PMA of each family, the shared diagnostics and the 100G path.

The line coding (sync headers, block types, scrambler, block lock) is explained in
[PHY interfaces and line coding](#phy-interfaces). The XGMII adapter is the one of
[XGMII](#par-xgmii).

## At a glance {#baser-glance}

| Class | FPGA | Line rate | PHY clock | Status |
|---------------------------------|-----------------|---------------|----------------|-------------------|
| `K7_GTX_5G_BASER` | Kintex-7 GTX | 5.15625 Gbps | 80.5664 MHz | Not recorded |
| `K7_GTX_10G_BASER` | Kintex-7 GTX | 10.3125 Gbps | 161.1328 MHz | Validated on hardware |
| `A7_GTP_5G_BASER` | Artix-7 GTP | 5.15625 Gbps | 161.1328 MHz | Build-tested |
| `USP_GTH_5G_BASER` | UltraScale+ GTH | 5.15625 Gbps | 78.125 MHz | Not recorded |
| `USP_GTY_5G_BASER` | UltraScale+ GTY | 5.15625 Gbps | 78.125 MHz | Not recorded |
| `USP_GTH_10G_BASER` | UltraScale+ GTH | 10.3125 Gbps | 156.25 MHz | Validated on hardware |
| `USP_GTY_10G_BASER` | UltraScale+ GTY | 10.3125 Gbps | 156.25 MHz | Validated on hardware |
| `USP_GTY_25G_BASER` | UltraScale+ GTY | 25.78125 Gbps | 390.625 MHz | In development |
| `GW5_10G_BASER` | GW5AST-138B GTR12 | 10.3125 Gbps | 161.1328 MHz | Validated on hardware |
| `USP_CMAC_100G` | UltraScale+ CMAC | 4 x 25.78125 Gbps | 322.2656 MHz | In development |

[Supported PHYs and FPGAs](#intro-phys) defines the status words. *Validated on hardware* covers
10GBASE-R; *Not recorded* means here that no hardware result is recorded at 5 Gbps. *In
development* marks the 25G PHY and the [100G section](#baser-100g): they pass their simulation
and construction tests, and their status changes faster than the rest.

Every BASE-R PHY has a 64-bit stream; the PHY clock is the rate of its user clock, given rounded
in the table. The Kintex-7, UltraScale+ and GW5 classes are exported by `liteeth.phy`; the
standalone generator accepts the Kintex-7 and UltraScale+ ones ([Integration](#integration)).
The Artix-7 class and the 100G wrapper are imported from their modules.

## Structure {#baser-structure}

A BASE-R PHY is a wrapper around four parts, shown in the figure below. The XGMII adapters
convert the MAC stream to XGMII characters and back. The PCS turns XGMII words into 66-bit blocks
and back. The PMA moves the blocks through the transceiver, and the wrapper adds the diagnostics
registers.

![BASE-R PHY: XGMII adapters between the 64-bit MAC stream and the PCS; the PCS encodes and
scrambles on transmit, finds the block boundary, monitors the error rate, descrambles and decodes
on receive, with an optional PRBS31 generator and checker; the PMA carries 66-bit blocks and slips one bit on request.](baser_blocks.svg)

The interface between the PCS and the PMA is a 64-bit block and its 2-bit sync header in each
direction, plus a receive bitslip request. The PCS contains no family-specific code: when a
transceiver cannot move one block per clock cycle, the wrapper enables the PCS only on the cycles
that move a block ([PMA per family](#baser-pma)).

The PMA owns the `eth_tx` and `eth_rx` clock domains, the transceiver PLL and its reset and
initialization. The PLL, DRP and reset helpers come from LiteICLink, except the Artix-7 GTP
initialization, which stays in `serial/gtp_7series.py`. The primitive parameters of each rate
stay visible in the PMA files.

## The PCS {#baser-pcs}

The PCS follows IEEE 802.3 Clause 49 (`PCS` in `baser/pcs/`). Its blocks are small and each one
maps to a part of the clause.

| Block | Function | Clause 49 |
|-------------------|---------------------------------------------------------------|----------------|
| Encoder | XGMII characters to 66-bit blocks, block types, ordered sets | 49.2.4 |
| Scrambler | Self-synchronizing, 1 + x^39 + x^58, payload only | 49.2.6 |
| PRBS31 generator | Test pattern 1 + x^28 + x^31 instead of the data | 49.2.8 |
| Block sync | Finds the block boundary from the sync headers | 49.2.9 |
| PRBS31 checker | Counts bit errors of a received test pattern | 49.2.12 |
| Decoder | Blocks to XGMII characters, block type checks | 49.2.4 |
| BER monitor | Invalid sync headers per window, `high_ber` | 49.2.13.3 |
| Watchdog | Qualifies the link status, requests a receiver reset | PCS_status (49.2.14.1), qualified over 16 windows |

### Block synchronization {#baser-blocksync}

A valid sync header is `01` (data) or `10` (control); `00` and `11` never occur on an aligned
stream. The block sync counts headers in windows of 64. The figure below shows its states.

![Block synchronization: while unlocked, an invalid header asks the PMA to slip one bit; 64 valid
headers in a window give block lock; once locked, 16 invalid headers in a window of 64 drop the
lock and slip again.](baser_blocksync.svg)

After a slip, the block sync ignores the headers for 8 cycles (`bitslip_low_cycles`), the time the
transceiver needs to apply it. The PMA implements the slip: the Xilinx transceivers shift their
gearbox by one bit, and the GW5 PMA drops one received bit.

### Error rate and link status {#baser-ber}

The BER monitor counts invalid sync headers. At 10 Gbps, 16 invalid headers within 125 µs set
`high_ber` (Clause 49); the 5 Gbps PHYs use the same values. At 25 Gbps the 25G class uses 97
invalid headers within 2 ms, the values of the 25GBASE-R Clause 107. Without block lock the monitor
holds in its initial state.

The watchdog judges 125 µs windows. A window is good when it holds at least one control header and
fewer than 1023 bad blocks. Sixteen good windows in a row raise the PCS status, which the wrapper
exports as `link_up`. Loss of block lock or a high error rate drops it at once.

Sixteen bad windows in a row make the watchdog request a receiver reset, on the theory that the
receiver failed in a way that slipping cannot fix. This qualification over 16 windows and the
reset request are implementation choices, not part of Clause 49; the wrapper sends the request to
the transceiver receiver initialization.

The PCS status means that this receiver decodes the partner: it does not prove a bidirectional
link. While its receiver is not synchronized, the PCS presents Local Fault ordered sets on its
receive XGMII, as Clause 49 specifies, and it passes received sequence ordered sets through. The
XGMII adapter ignores these words, and nothing tells the link partner, because the local and
remote fault state machine is not implemented.

### Pipelining {#baser-pipelining}

`with_pipelining` adds register stages for timing closure. `True` registers the transmit encoder
and the receive block classification; `2` also registers the per-lane classification and
decoding, with one more cycle of receive latency. The UltraScale+ classes turn it on above
200 MHz, so at 25 Gbps; the Artix-7 class always uses `True` and the GW5 class `2`.

## PMA per family {#baser-pma}

The transceivers differ in how their 64b/66b gearbox moves blocks. UltraScale+ moves one block per
user clock; the 7-Series transceivers pause one cycle in 33; the GW5 transceiver has no 64b/66b
gearbox at all.

| Family | User clock | Blocks per cycle | PCS enable |
|---------------------|----------------------|--------------------------|-----------------------|
| UltraScale+ GTH, GTY | line rate / 66 | One | Always |
| Kintex-7 GTX | line rate / 64 | 32 in 33 cycles | `tx_ce`, `rx_ce` |
| Artix-7 GTP | line rate / 32 | 16 in 33 cycles | `tx_ce`, `rx_ce` |
| GW5 GTR12 | line rate / 64 | 32 in 33 cycles | `tx_ce`, `rx_ce` |

**UltraScale+.** The transceiver gearbox takes a block and its header on every user clock cycle:
156.25 MHz at 10 Gbps, 78.125 MHz at 5 Gbps and 390.625 MHz at 25 Gbps.

The wrapper creates the quad PLL from the reference clock, 156.25 MHz by default, unless the design
passes one ([Sharing a QPLL](#baser-qpll)). `refclk_from_fabric` takes a reference that comes from
the fabric.

The 25G class needs QPLL0 with the 25G tuning of its PMA. From a 156.25 MHz reference the QPLL
runs fractional-N; a 161.1328125 MHz reference gives integer-N, preferable for jitter. Both pass
construction tests, which do not establish signal integrity or timing closure on a new board.

**Kintex-7.** The 7-Series gearbox works with 64-bit words at line rate / 64, but a block
is 66 bits: 33 words carry 32 blocks, so one cycle in 33 is a pause.

The PMA drives `tx_ce` and `rx_ce` on the cycles that move a block. The wrapper puts the PCS under
a clock enable from these signals, and gates the XGMII adapters and the MAC stream the same way.

**Artix-7.** The GTP gearbox has a 32-bit user interface, so each block crosses it as two words:
first the upper 32 bits with the header, then the lower 32 bits. The sequence counter runs from 0
to 32 with the pause at 31, so 33 cycles carry 16 blocks, and `tx_ce` is high once per block.

The figure below compares both cadences.

![7-Series gearbox cadence over the 33-cycle sequence: the Kintex-7 GTX moves one block per cycle
and pauses at 32; the Artix-7 GTP moves one half block per cycle, upper half first, and pauses at
31.](baser_gearbox.svg)

The Artix-7 PMA needs TXUSRCLK and RXUSRCLK at 322.265625 MHz and USRCLK2 at 161.1328125 MHz, from
two MMCMs of the PMA, one per direction. The clock enables qualify the block transfers, so the PCS
needs no gated clock and no multicycle constraint.

**GW5.** The GW5AST-138 transceiver lanes have no 64b/66b gearbox, so the PMA implements both in
fabric ([GW5 10GBASE-R](#baser-gw5)). The transmit gearbox shifts blocks into a two-block window
and the receive gearbox extracts blocks from a three-word window, each on 32 of every 33 cycles.
The GW5 PMA thus follows the same 32-in-33 cadence as the Kintex-7, with its fabric gearboxes.

### Artix-7 reference clocks {#baser-a7-clocks}

The Artix-7 class does not create its PLL: the design passes one channel of the GTP common block,
as for [Artix-7 BASE-X](#basex-xilinx), with settings that match its reference. At 5.15625 Gbps
the PLL must run at 2.578125 GHz, half the line rate, which leaves few references.

| Reference | `fbdiv` | `fbdiv_45` | Example |
|------------------|---------|------------|--------------------------------------------|
| 103.125 MHz | 5 | 5 | AC701, from its Si5324 |
| 128.90625 MHz | 4 | 5 | External reference |
| 161.1328125 MHz | 4 | 4 | External reference |
| 171.875 MHz | 3 | 5 | Acorn Baseboard Mini, from a fabric PLL |

All rows use `refclk_div` 1; the PLL output is the reference times `fbdiv` times `fbdiv_45`.
125 MHz and 156.25 MHz cannot reach this rate with the integer dividers. The design also passes
the frequency to the PHY as `refclk_freq`, for its calibration dividers; this argument does not
program the PLL.

The bench targets `bench/ac701_5g.py` and `bench/acorn_baseboard_mini_5g.py` connect this PHY to
a 32-bit Etherbone core at 100 MHz. Such a core cannot sustain every 5G traffic pattern: these
targets serve bring-up. [Examples and targets](#examples) describes their clocking, SFP setup and
PHY reset.

## Sharing a QPLL {#baser-qpll}

Several channels of a quad can share one quad PLL. The PLL must then have exactly one reset
owner: resetting it interrupts every channel that uses it. The design instantiates the PLL once,
passes it to each PHY, and marks one PHY as the owner, as the figure below shows.

![Two BASE-R channels sharing a QPLL: the parent design instantiates the PLL from the reference
clock and passes it to both PHYs; PHY 0 owns the PLL reset, PHY 1 resets its own channel
only.](baser_qpll.svg)

The owner takes `pll_master=True`, the default, and the other PHYs `pll_master=False`. A follower
resets its own channel without resetting the PLL. If the design manages the PLL reset itself, all
the PHYs take `pll_master=False`. The design also renames the Ethernet clock domains of each PHY
when it instantiates several of them.

The Kintex-7 classes always take their PLL from the design, as `qpll`. The UltraScale+ classes
create their own PLL unless the design passes one as `pll`; a follower without a `pll` raises an
error. For an external 25G PLL, the design copies the tuning of the PMA:

```python
pll = GTYQuadPLL(refclk, refclk_freq, 25.78125e9,
    qpll="qpll0", qpll_params=PMA_USP_GTY_25G_BASER.qpll_params)
phy = USP_GTY_25G_BASER(None, data_pads, sys_clk_freq, pll=pll)
```

The wrapper checks the family, line rate, PLL selection and tuning of the PLL it receives. It
does not modify the PLL; the design configures the reference clock on it.

## Diagnostics registers {#baser-diagnostics}

All BASE-R PHYs share one set of diagnostics registers (`LiteEthBASERPHY`), present with
`with_csr`. They give the receive state, a transceiver loopback and a PRBS31 bit error test.

| Register | Field | Bits | Content |
|------------------------|----------------------------|------|--------------------------------------|
| `reset` | | 0 | PHY reset, transceiver and PCS |
| `control` | `loopback` | 0-2 | Transceiver loopback mode |
| `control` | `tx_prbs31_enable` | 3 | Transmit the PRBS31 pattern |
| `control` | `rx_prbs31_enable` | 4 | Check the received PRBS31 pattern |
| `control` | `prbs_pause` | 5 | Freeze the PRBS error counter |
| `status` | `block_lock` | 0 | Block boundary found |
| `status` | `high_ber` | 1 | Error rate above the threshold |
| `status` | `link_up` | 2 | PCS status: block lock, no high error rate |
| `status` | `error_count` | 3-9 | PRBS31 bit errors in one sampled block |
| `status` | `block_lock_lost` | 10 | Block lock lost since the last read |
| `status` | `high_ber_latched` | 11 | High error rate seen since the last read |
| `status` | `prbs_paused` | 12 | Frozen PRBS counter has reached sys |
| `rx_prbs_errors` | | 0-31 | PRBS31 bit errors since the checker was enabled |

Reading `status` clears its two sticky bits. The loopback values follow the transceiver user
guides: 0 off, 1 near-end PCS, 2 near-end PMA, 4 far-end PMA, 6 far-end PCS. The GW5 PHY does not
support the loopback field, because its transceiver configuration sets the loopback mode.

**PRBS test.** With both ends sending PRBS31, the checker counts bit errors. The `rx_prbs_errors`
counter saturates instead of wrapping; on the 7-Series and GW5 PHYs it advances only on the cycles
that carry a block. Disabling the checker or resetting the receive domain clears it, even while
paused.

The counter lives in `eth_rx` and is wider than one register word on a narrow bus. To read it
coherently, software freezes it first:

1. Clear `prbs_pause` and wait for `prbs_paused` to read 0.
2. Enable the checker and leave it enabled during the whole measurement.
3. Set `prbs_pause` and poll `prbs_paused` until it reads 1.
4. Read `rx_prbs_errors`: the acknowledge crossed to sys together with the frozen value.
5. Clear `prbs_pause` and wait for `prbs_paused` to read 0 before the next snapshot.

Polling needs a timeout if the receive clock can stop. For a bit error ratio, the number of bits
is the elapsed time times the block rate times 66. [Debugging](#debugging) uses these registers
to bring up a link.

## GW5 10GBASE-R {#baser-gw5}

The GW5 PHY connects the BASE-R PCS to a `GTR12_QUAD` transceiver of the GW5AST-138B, through its
raw 64-bit interface at 10.3125 Gbps (`GW5_10G_BASER`). It supports Q1 lane 0 or 1, selected with
`lane`, with a 125 MHz differential reference on Q1 REFCLK0. On the Sipeed Tang Mega 138K Pro
dock, below, these lanes are the two SFP+ cages.

![Sipeed Tang Mega 138K Pro: GW5AST-138B FPGA, one RJ45 port and two SFP+ cages (SFP-0, SFP-1)
driven by the GTR12 transceiver lanes.](tang_mega_138k_pro.jpg){width=45%}

The transceiver uses a 16-bit internal width and a 1:4 fabric ratio. The fabric word is 64 bits
at line rate / 64, 161.13 MHz, carried in the low 8 bits of eight 10-bit slots, slot 0 first. The
PCS uses the bit order of the Xilinx transceiver gearboxes; the GW5 gearboxes convert it to the
transmission order.

To close timing at 161.13 MHz, the XGMII words are registered between the adapters and the PCS,
the PCS uses `with_pipelining=2` and the XGMII receive adapter is pipelined. These stages only add
latency. The PRBS31 generator and checker are off by default to save timing; `with_prbs=True`
adds them for bring-up.

| Register | Content |
|-----------------|------------------------------------------------------------------|
| `reset` | Resets the lane and the PCS |
| `control`, `status` | [Diagnostics registers](#baser-diagnostics), without loopback |
| `serdes_status` | Transmit PLL lock, receive CDR lock |

**Transceiver configuration.** As for [GW5 1000BASE-X](#basex-gw5), `baser/pma/gw5_config.py` embeds
the hardware-validated configuration writes and their TOML source, and the Gowin build loads them
from `gw5_10gbaser.csr`. The writes come from the Gowin 1.9.12.04 generator, and older tools reject
some of them.

The generated writes are used unchanged except one. The generator programs a CDR VCO bias that
does not lock to 10GBASE-R link partners on the Tang Mega 138K Pro, so the PHY overrides that
single register write with a hardware-validated value.

10.3125 Gbps cannot come from the 100 MHz reference of 1000BASE-X. The embedded configuration uses
125 MHz; the transceiver also accepts 156.25 MHz, with a regenerated configuration.

On the Tang Mega 138K Pro, the LiteX-Boards target selects this PHY. The command below builds and
loads Etherbone over SFP-0; `--eth-sfp=1` selects SFP-1. Success is a reply to `ping` from a
10GBASE-R link partner.

```sh
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=10gbaser --eth-ip=192.168.1.50 --build --load
ping 192.168.1.50
```

The dock must keep its 125 MHz reference clock setting on its second clock generator, PLL1. The
board target rejects 10GBASE-R together with PCIe, since the configuration owns the quad.

## Capabilities and limits {#baser-limits}

- **No FEC.** Neither BASE-R FEC nor RS-FEC is implemented, and no transceiver parameter enables
  them. The link partner and the module must run without FEC.
- **Fixed speed.** The rate is chosen at construction. Autonegotiation and link training are not
  implemented, so a raw serial rate alone does not make a backplane or direct-attach copper link
  interoperate.
- **Faults.** The receive side presents Local Fault to the MAC while unsynchronized; no fault is
  signalled to the link partner. `link_up` is the PCS receive status only.
- **Receive errors.** The XGMII receive adapter does not flag the /E/ characters that the PCS
  substitutes for errored blocks; only the FCS check of the MAC catches these frames.
- **Inter-frame gap.** The XGMII adapter enforces the gap with the deficit idle count, and the
  BASE-R wrappers do not declare `integrated_ifg_inserter`, so the MAC adds its own gap too
  ([MAC](#mac)). In simulation the gap is then 16 to 20 bytes for frames of 60 to 64 bytes,
  instead of 12, which lowers the packet rate on short frames; it also hides the shortfall of the
  deficit idle count ([XGMII](#par-xgmii)).
- **Throughput.** A faster PHY does not make a slower datapath faster. The packet processing
  clock, the stream width and the packet rate decide what the core sustains
  ([Performance and resources](#performance)).
- **25G.** In development; the 390.625 MHz user clock is also the clock that the MAC datapath
  must close timing at.
- **Artix-7 and GW5.** Not in the standalone generator; the Artix-7 class is build-tested only.

## 100G {#baser-100g}

**In development:** the 100G path builds and runs in simulation, and its hardware validation is
pending. Its code, options and status change faster than the rest of LiteEth. This section gives
its architecture, how to try it and its limits.

LiteEth reaches 100 Gbps through the CMAC hard block of UltraScale+ devices, which holds the MAC,
the PCS and the transceivers. LiteEth adds a frame adapter between its stream and the AXI-Stream
of the CMAC, as the figure below shows.

![100G path: the UDP/IP core or user logic connects to the AXI adapter of USP_CMAC_100G, which
feeds the generated CMAC IP and its four GTY lanes at 25.78125 Gbps; the module on the other side
runs without FEC.](baser_100g.svg)

### Frame adapter {#baser-100g-adapter}

The adapter connects an AXI-Stream to the PHY-side stream of LiteEth (`LiteEthMACAXIStream`), at
64, 128, 256 or 512 bits; the CMAC wrapper uses 512 bits. Frames start at the destination MAC
address and carry no FCS. Byte lane 0 comes first, and `be` maps to `tkeep`.

On transmit, the padding inserter and a packet FIFO commit a frame of at least 60 bytes before
sending it. The hard MAC adds the FCS, the preamble and the gap, so the adapter turns off these
stages of the LiteEth MAC.

On receive, the CMAC gives no back-pressure: a drop FIFO discards whole frames on overflow, AXI
errors, malformed masks, runts or oversize frames.

Three 32-bit wrapping counters in `eth_rx` count received frames, dropped frames and bad frames
(`rx_packets`, `rx_drops`, `rx_bad_frames`); bad frames are a subset of drops. They are signals,
not registers: a design that exposes them crosses them to sys coherently.

### CMAC wrapper {#baser-100g-cmac}

`USP_CMAC_100G` instantiates a CMAC IP that the target generates with `bench/cmac_100g.tcl`, after
setting the IP name, the CMAC site and the GT group. The target owns the pins, the reference
clock buffer placement, the constraints, the module management and the link partner setup.

| Setting | Value |
|---------------------------|-----------------------------------------------------|
| IP name | `liteeth_cmac` by default |
| Lanes | Four GTY lanes, CAUI-4, NRZ |
| Reference clock | 161.1328125 MHz |
| Initialization clock | Free-running 100 MHz |
| AXI clocks | CMAC transmit user clock, 322.265625 MHz, for both directions |
| Maximum frame | 9022 bytes including FCS |

Reception is always enabled. Until the receive lanes align, the transmitter sends remote fault and
keeps the queued frames. `link_up` is synchronized to sys; the other status signals stay in their
CMAC domain. Autonegotiation, link training, RS-FEC, PTP and pause frames are disabled.

### How to try it {#baser-100g-try}

From the repository root, the first command writes the CMAC project and the second synthesizes it
out of context with Vivado; on the default `xcvu3p-ffvc1517-2-e`, synthesis resolves one CMAC, four
GTY channels and one GTY common block.

```sh
PYTHONPATH=. python3 bench/cmac_100g.py --output-dir build/cmac_100g
cd build/cmac_100g
vivado -mode batch -source build.tcl
```

A Verilator bench runs the full UDP/IP and adapter path with independent clocks and finite queues
(`bench/mac_100g.py`). Its profiles send minimum, standard and jumbo UDP payloads of 18, 1472 and
8972 bytes, or a mix with stalls, errors and resets.

```sh
PYTHONPATH=. python3 bench/mac_100g.py --profile standard --packets 128
PYTHONPATH=. python3 bench/mac_100g.py --profile minimum --packets 128
PYTHONPATH=. python3 bench/mac_100g.py --profile mixed --packets 64 --stall-cycles 200 \
    --error-every 13 --reset
```

The bench checks bytes, metadata, masks, ordering and loss, and writes JSON results with packet
rates, loss, errors and the maximum latency. A short run can fit in the queues: run enough packets
to see a sustained overload. `bench/mac_100g_timing.py` writes a routed timing build of the
adapter path, whose recorded result is in [Performance and resources](#performance).

### Native 100G PCS model {#baser-100g-pcs}

A native 100GBASE-R PCS would keep the same frame interface and replace the CMAC. Its first step
is a Python model of IEEE 802.3 Clause 82 without FEC (`test/model/pcs_100g.py`), not
synthesizable. It distributes 66-bit blocks over 20 logical PCS lanes, inserts an alignment marker
every 16383 blocks per lane, and multiplexes five logical lanes on each of four physical lanes.

The aggregate scrambler and the 20 logical lanes prevent treating 100GBASE-R as four 25G PCS
instances. Modules that need RS-FEC (Clause 91), such as SR4 or CR4, are outside the scope of the
model; a no-FEC module over CAUI-4, such as LR4, matches it.

The tests check it against the published marker and parity values, and an exporter writes test
vectors:

```sh
python3 -m pytest -q test/test_pcs_100g_model.py
python3 -m test.model.pcs_100g --output vectors.json
```

### Limitations {#baser-100g-limits}

- Hardware validation of the CMAC wrapper is pending.
- The UDP/IP core does not sustain 100 Gbps with minimum-size frames.
- No FEC: the module and the link partner must run without it.
- The CMAC wrapper has no standalone generator entry, and the timing margin of the adapter path
  requires a timing and CDC review on each target.

## In the code {#baser-code}

| File | Content |
|------------------------------------------|-----------------------------------------------------|
| `pcs/__init__.py` | `PCS`: transmit and receive paths |
| `pcs/encoder.py`, `decoder.py` | XGMII to 66-bit blocks and back |
| `pcs/scrambler.py` | Scrambler and descrambler |
| `pcs/block_sync.py` | Block synchronization |
| `pcs/ber_mon.py` | BER monitor |
| `pcs/watchdog.py` | Link status qualification, receiver reset request |
| `pcs/prbs.py` | PRBS31 generator and checker |
| `pma/gtx_7series.py` | Kintex-7 GTX PMAs, 5G and 10G |
| `pma/gtp_7series.py` | Artix-7 GTP 5G PMA |
| `pma/gth_usp.py`, `gty_usp.py` | UltraScale+ PMAs, 5G, 10G and 25G |
| `pma/gw5.py`, `gw5_config.py` | GW5 gearboxes, PMA, embedded configuration |
| `wrappers/diagnostics.py` | `LiteEthBASERPHY`: registers and PRBS counter |
| `wrappers/k7_gtx.py` | `K7_GTX_5G_BASER`, `K7_GTX_10G_BASER` |
| `wrappers/a7_gtp.py` | `A7_GTP_5G_BASER` |
| `wrappers/usp_gt.py` | UltraScale+ 5G, 10G and 25G classes |
| `wrappers/gw5.py` | `GW5_10G_BASER` |
| `wrappers/usp_cmac.py` | `USP_CMAC_100G` |
| `mac/axis.py` | `LiteEthMACAXIStream` |

The PHY files are in `liteeth/phy/serial/baser/`, the adapter in `liteeth/`. The tests include
`test/test_pcs_baser.py`, `test/test_xgmii_phy.py`, `test/test_baser_diagnostics.py`,
`test/test_baser_qpll.py`, `test/test_a7_baser.py`, `test/test_gw5_10gbaser.py`,
`test/test_cmac_100g.py` and `test/test_mac_100g.py`. They do not model analog CDR behaviour,
cable loss or transceiver calibration.

Where to go next: [PHY interfaces and line coding](#phy-interfaces) for 64b/66b,
[Integration](#integration) for the standalone generator and porting a transceiver PHY,
[Examples and targets](#examples) for the benches, [Debugging](#debugging) for a link that does
not lock.
