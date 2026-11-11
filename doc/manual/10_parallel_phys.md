```{=latex}
\part{The PHYs}
```

```{=html}
<h1 class="part">Part III. The PHYs</h1>
```

# MII, RMII, GMII, RGMII, XGMII and 10BASE-T {#parallel-phys}

A parallel PHY connects the MAC to an external PHY through a parallel interface: 4 bits for MII,
2 for RMII, 8 for GMII, 4 on both clock edges for RGMII, 64 for XGMII. It only moves bytes between
the MAC stream and the pins; the external PHY does the line coding.

This chapter describes each of these PHYs, plus the experimental 10BASE-T PHY that drives the
twisted pairs from FPGA pins.

The interfaces themselves (signals, clocks, delays) are explained in
[PHY interfaces and line coding](#phy-interfaces). The serial PHYs, which do the line coding in
the FPGA, are in [1000BASE-X, 2500BASE-X and SGMII](#basex-phys) and
[5G, 10G and 25G BASE-R, and 100G](#baser-phys).

## At a glance {#par-glance}

| Class | Interface | FPGA | PHY clock | Status |
|--------------------------------------|--------------------|----------------|---------------|----------------------|
| `LiteEthPHYMII` | MII, 10/100 Mbps | Any | 25 MHz | Validated on hardware |
| `LiteEthPHYRMII` | RMII, 10/100 Mbps | Any | 50 MHz | Validated on hardware |
| `LiteEthPHYGMII` | GMII, 1 Gbps | Spartan-6, 7-Series | 125 MHz | Validated on hardware |
| `LiteEthPHYGMIIMII` | GMII with MII fallback | Spartan-6, 7-Series | 125 or 25 MHz | Validated on hardware |
| RGMII classes | RGMII, 1 Gbps | Per family | 125 MHz | Per family |
| `LiteEthPHYXGMII` | XGMII, 10 Gbps | Any | 156.25 MHz | Not recorded |
| `LiteEthPHY10BASET` | 10BASE-T on FPGA pins | Any | 60 MHz or more | Experimental |
| `LiteEthPHYModel` | Simulation | Simulator | sys | Simulation only |

[Supported PHYs and FPGAs](#intro-phys) defines the status words. The GMII PHYs need Xilinx clock
buffers, and their recorded boards are Spartan-6 and 7-Series ones. The 10BASE-T PHY is
experimental: it is not an IEEE 802.3 compliant transceiver and no hardware result is recorded for
it. The XGMII status covers an external XGMII PHY; the same XGMII encoder runs inside every BASE-R
PHY ([BASE-R PHYs](#baser-phys)).

All these classes are exported by `liteeth.phy`, except the GW5, Titanium and Trion RGMII PHYs,
which a design imports from their modules, and the simulation model, imported from
`liteeth.phy.model`. The standalone generator accepts MII, RMII, 10BASE-T,
GMII, GMII/MII, XGMII and the 7-Series, ECP5 and UltraScale RGMII PHYs
([Integration](#integration)).

## The PHY contract {#par-contract}

Every LiteEth PHY, parallel or serial, presents the same interface to the MAC. The MAC reads a
few attributes of the PHY to adapt its own datapath ([MAC](#mac)), so a design changes the PHY
class and nothing else.

| Attribute | Content |
|-----------------------------------|--------------------------------------------------------|
| `sink`, `source` | Transmit and receive streams: `data`, `be`, `error`, `last` |
| `dw` | Stream width in bits: 8 for the parallel PHYs, 64 for XGMII |
| `crg` (or the PHY itself) | Clock domains `eth_tx` and `eth_rx`; the PHY reset register |
| `tx_clk_freq`, `rx_clk_freq` | Clock frequencies used for the timing constraints |
| `mdio` | MDIO registers, when the pads have `mdc` and `mdio` |
| `with_preamble_crc` | False when the PHY does not want preamble and FCS |
| `integrated_ifg_inserter` | True when the PHY enforces the inter-frame gap itself |
| `tx_gap_cycles` | Gap length in `eth_tx` cycles, when the PHY sets it per speed |

The parallel PHYs carry the whole frame, preamble and FCS included: the MAC inserts and checks
them ([MAC](#mac)). Most of them accept one byte per clock cycle. The MII and RMII PHYs, and the
ECP5 RGMII PHY below 1 Gbps, accept a byte only every few cycles and pace the MAC with `ready`.

On receive, `last` marks the last byte of the frame, found when the data valid signal of the
external PHY falls. The
`error` flag carries the receive error of the external PHY where the PHY decodes it, on RMII and
RGMII. The MAC carries this flag to the end of the frame ([MAC](#mac)).

## MDIO and PHY reset {#par-mdio}

The external PHY has a management interface, MDIO, made of a clock (MDC) and a bidirectional data
pin. LiteEth drives it by software: one register holds the clock, the output enable and the output
bit, another returns the input bit (`LiteEthPHYMDIO`). The CPU toggles the clock with register
writes, which is slow but small.

| Register | Field | Role |
|-----------------|-----------|---------------------------------------|
| `mdio_w` | `mdc` | MDC pin level |
| `mdio_w` | `oe` | Drive the MDIO pin |
| `mdio_w` | `w` | MDIO output bit |
| `mdio_r` | `r` | MDIO input bit, synchronized to sys |

The LiteX BIOS uses these registers for its `mdio_read`, `mdio_write` and `mdio_dump` commands
([Integration](#integration)). A PHY creates the MDIO registers only when its pads have `mdc` and
`mdio`.

Each PHY also drives the reset pin of the external PHY (`rst_n`) when the pads have one (the
clock pads on Trion and Titanium); the Agilex PHY holds it high with `with_phy_reset=False`, and
the XGMII PHY drives none. The
reset is the OR of a `reset` register in the PHY clock module and, by default, a power-up pulse
of 256 sys cycles (`LiteEthPHYHWReset`). The same reset holds the `eth_tx` and `eth_rx` domains,
so writing the register restarts both the external PHY and the PHY logic.

Most constructors take `with_hw_init_reset` to disable the power-up pulse, for a board that
resets its PHY by other means. The XGMII PHY accepts it but has no reset output and no MDIO
registers. The RGMII PHYs for 7-Series, Titanium, Trion and Agilex also take
`hw_reset_cycles` to lengthen it.

## Autodetection {#par-autodetect}

`LiteEthPHY()` returns a PHY instance from the board pads. Pads with a `gtx` clock and 8 data
bits give GMII, or GMII/MII when a `tx` clock is also present. Pads with 4 data bits give MII.
RGMII pads raise an error, because the RGMII PHYs depend on the FPGA family: a design instantiates
the class of its family directly.

## MII {#par-mii}

MII carries 4 bits per clock, at 25 MHz for 100 Mbps or 2.5 MHz for 10 Mbps. The external PHY
drives both clocks, `tx` and `rx`, so a speed change needs nothing from the FPGA. The PHY
converts the 8-bit MAC stream to nibbles on transmit and back on receive (`LiteEthPHYMII`).

The transmit converter takes a byte every two clock cycles and drives TX_EN and TXD from
registers. TX_ER, when present, is held low. The receive side resets its nibble assembler while
RX_DV is low, so that each frame starts on a byte boundary.

Some boards feed the external PHY with a 25 MHz clock from the FPGA. When the clock pads have a
`phy` pin, the PHY drives it by toggling a register in a `base50` clock domain, which the design
provides at 50 MHz.

## RMII {#par-rmii}

RMII carries 2 bits per clock at 50 MHz for both speeds, with a shared reference clock. At
10 Mbps each dibit lasts ten clock cycles. RX_DV and carrier sense share one pin, CRS_DV. The PHY
converts between the MAC bytes and dibits and detects the speed from the received frames
(`LiteEthPHYRMII`).

**Reference clock.** By default the PHY runs from a 50 MHz clock domain of the design, named by
`refclk_cd` (`"eth"`), and outputs it on the `ref_clk` pin through a DDR output. With
`refclk_cd=None`, the PHY uses the `ref_clk` pin as its clock input instead, for boards where the
external PHY or an oscillator provides the clock.

**Speed detection.** RMII does not signal the speed on the wires. The receiver measures the
preamble: it counts the cycles between CRS_DV rising and the first set bit of the received data.
Fewer than 20 cycles means 100 Mbps, more means 10 Mbps. The transmitter reuses the speed of the
last received frame. Until a frame has been received, the speed is 10 Mbps, so the frames sent
first on a 100 Mbps link are lost.

**Frame boundaries.** A frame starts on CRS_DV high with non-zero data. It ends on two
consecutive low samples of CRS_DV, because the external PHY toggles CRS_DV at the end of a frame.
A two-entry delay line aligns the data with this late decision. RX_ER, when present, sets `error`
until the end of the frame.

## GMII and GMII/MII {#par-gmii}

GMII carries 8 bits per clock at 125 MHz for 1 Gbps. The FPGA drives the transmit clock (GTX_CLK)
with a DDR output, and the external PHY drives the receive clock. The PHY registers the pads on
both sides (`LiteEthPHYGMII`). Its clock module instantiates Xilinx `BUFG` buffers, so this class
targets Xilinx FPGAs unless it runs as a model.

The `eth_tx` domain is clocked from the receive clock of the external PHY, so the external PHY
must provide its receive clock for the FPGA to transmit. With `model=True`, both domains run
from sys, for simulation with the LiteX GMII model.

The GMII/MII PHY serves external PHYs that fall back to MII at 10 and 100 Mbps
(`LiteEthPHYGMIIMII`). It instantiates both datapaths and switches between them with a mode bit.
In MII mode it stops driving GTX_CLK and clocks `eth_tx` from the MII transmit clock of the
external PHY.

The mode comes from a frequency measurement. Every 1024 cycles of the receive clock, a pulse
crosses to sys, where a counter measures the period. A period longer than 1024 cycles of 125 MHz
plus 5 % selects MII. The `mode` register of the detector shows the result; the constructor needs
the sys frequency (`clk_freq`).

## RGMII {#par-rgmii}

RGMII carries GMII over 4 data bits and one control bit, on both edges of a 125 MHz clock. The
control pin carries RX_DV on the rising edge and RX_DV XOR RX_ER on the falling edge, and the same
for transmit ([PHY interfaces and line coding](#phy-interfaces)).

The clock must sit in the middle of the data, which needs about 2 ns of delay in each direction.

This delay comes from the external PHY, the board traces or the FPGA. The LiteEth RGMII PHYs add
it in the FPGA by default, with the delay elements of each family. The figure below shows the
7-Series PHY: DDR outputs, a PLL that shifts the transmit clock by a quarter period, and fixed
input delays.

![7-Series RGMII PHY: the MAC stream goes through DDR outputs to the external PHY; a PLL clocked
from RXC shifts the transmit clock by 90 degrees (2 ns at 125 MHz); the received data goes
through fixed 2 ns input delays and DDR inputs. The blue path is the transmit
data.](par_rgmii.svg)

When the external PHY or the board already adds the delays, the design sets `tx_delay` or
`rx_delay` to 0 on the families that take them. Adding the delay twice puts the clock edge near a
data transition, and the link fails or shows CRC errors.

### Per family {#par-rgmii-families}

| Class or module | FPGA | Delays | Status |
|------------------------------------------------|------------------------|------------------------------|----------------------|
| `LiteEthS6PHYRGMII` | Spartan-6 | `IODELAY2`, 50 ps taps | Validated on hardware |
| `LiteEthS7PHYRGMII` | 7-Series | PLL phase, `IDELAYE2` | Validated on hardware |
| `LiteEthUSPHYRGMII` | UltraScale | PLL phase, `IDELAYE3` | Validated on hardware |
| `LiteEthECP5PHYRGMII` | ECP5 | `DELAYG`, 25 ps taps | Validated on hardware |
| `rgmii/gw5.py` | Gowin GW5A | `IODELAY`, 12.5 ps taps | Validated on hardware |
| `rgmii/trion.py` | Efinix Trion | PLL phase 45 degrees | Validated on hardware |
| `rgmii/titanium.py` | Efinix Titanium | PLL phases 90, 270 degrees | Build-tested |
| `LiteEthAgilexPHYRGMII` | Agilex 3, Agilex 5 | PLL phase, 2 ns only | Build-tested |

[Supported PHYs and FPGAs](#intro-phys) defines the status words. The default delays are 2 ns in
both directions on every family that takes `tx_delay` and `rx_delay`.

Most PHYs clock `eth_tx` from the receive clock of the external PHY, directly or through a PLL
that also makes the shifted copy for TXC. The ECP5 and GW5 PHYs can take a transmit clock of the
design instead (`tx_clk`), and the Agilex PHY always does (`ref_tx_clk`).

**Xilinx.** The 7-Series and UltraScale PHYs turn `tx_delay` into a PLL phase. The 7-Series PHY
turns `rx_delay` into fixed `IDELAYE2` taps, computed from the IDELAYCTRL reference,
`iodelay_clk_freq` (200, 300 or 400 MHz). The `IDELAYE2` has 32 taps, so the default 2 ns needs
200 MHz; at 300 or 400 MHz the constructor fails unless `rx_delay` is at most about 1.6 or
1.2 ns. The UltraScale PHY passes `rx_delay` in picoseconds to `IDELAYE3`; it takes a 300 MHz
reference by default, and `usp=True` on UltraScale+.

The design instantiates the IDELAYCTRL of the bank with that reference clock; the PHY does not.
The Spartan-6 PHY uses fixed `IODELAY2` taps in both directions and no PLL.

**ECP5.** The ECP5 PHY uses `DELAYG` elements on the clock output and on the receive pins. It
also decodes the in-band status that the external PHY sends between frames: link, speed (10, 100
or 1000 Mbps) and duplex. The `inband_status` register shows it (`with_inband_status`, on by
default).

With `with_dynamic_link=True`, the ECP5 PHY follows the speed of this in-band status, at 10 and
100 Mbps too. At these speeds RGMII sends each nibble on both edges, so the PHY splits each byte
over two clock periods. It also sets `tx_gap_cycles`, which the MAC uses to scale the inter-frame
gap with the speed.

| Transmit clock | 1 Gbps | 100 Mbps | 10 Mbps |
|----------------------|-------------------|-----------------------------|------------------------------|
| RX clock (default) | 12-cycle gap | 24-cycle gap | 24-cycle gap |
| `tx_clk` at 125 MHz | 12-cycle gap | TXC = `tx_clk`/5 (25 MHz), 120-cycle gap | TXC = `tx_clk`/50 (2.5 MHz), 1200-cycle gap |

With the receive clock, which follows the speed, each byte takes two cycles and the gap doubles.
With a fixed 125 MHz `tx_clk` from the design, the PHY generates the slower TXC by division and
sends one nibble per divided period.

**Gowin GW5.** The GW5 PHY follows the ECP5 one with the `IODELAY` primitive. Its transmit clock
delay is also a register, `txdelay_taps`, for tuning on the board. It runs at 1 Gbps only, without
in-band status decoding.

**Efinix.** The Trion and Titanium PHYs take the platform, to create the PLL of the family. Their
delays are fixed PLL phases, not parameters. Their clock domains are named after an instance
counter, `eth0_tx` and `eth0_rx` for the first one, so that two PHYs can coexist. LiteX
`add_ethernet` and `add_etherbone` take the prefix of these names as `phy_cd`.

**Agilex.** The Agilex PHY generates the transmit clocks from a 125 MHz `ref_tx_clk` of the
design, with an `Agilex3PLL` or `Agilex5PLL` chosen from the device name. It accepts only a 2 ns
transmit delay, a 90 degree phase; its `rx_delay` argument has no effect.

### Receive errors {#par-rgmii-errors}

An RGMII receiver recovers RX_DV from the rising sample of RX_CTL and RX_ER from the XOR of both
samples. The PHYs decode both after the same alignment and pipeline stages as the data nibbles,
so the error lines up with its byte.

An error sets `error` on the stream and holds it until the end of the frame; it clears during idle.

The hold keeps the error visible after the MAC has removed the preamble and FCS and converted the
width. On the ECP5 PHY at 10 and 100 Mbps, an error on either nibble marks the assembled byte, and
the idle cycles between nibbles do not clear it. RX_ER outside a frame neither starts a frame nor
marks the next one.

The tests of these rules put errors at the frame boundaries and in the payload, check that a
valid FCS does not hide a PHY error, and send a clean frame after an errored one to check that
the flag clears (`test/test_rgmii_rx.py`).

## XGMII {#par-xgmii}

XGMII connects a 10 Gbps MAC to an external PHY or to a hard PCS. LiteEth uses its 64-bit form:
eight byte lanes with one control bit each, sampled on one edge of a 156.25 MHz clock
(`LiteEthPHYXGMII`). The stream width is 64 bits; the constructor asserts it.

**Transmit.** The PHY replaces the first preamble byte with the start character (`0xFB`), ends
the frame with a terminate character (`0xFD`) and fills the gaps with idles (`0x07`). A frame may
only start on lane 0 or lane 4 of a word, so the PHY shifts the data by four bytes when it starts
on lane 4.

**Inter-frame gap.** The start alignment makes a gap of exactly 12 bytes impossible after most
frames. The PHY implements a deficit idle count, the rule of IEEE 802.3 subclause 46.3.1.4:
remove up to 3 idles after a frame and add them back later, so that the average gap stays at 12
bytes.

The table below gives the rule, as the code writes it: the gap and the new count for each frame
length modulo 4 and each count before the frame.

| Length mod 4 | Count 0 | Count 1 | Count 2 | Count 3 |
|--------------|-------------|-------------|-------------|-------------|
| 0 | 12, new 0 | 12, new 1 | 12, new 2 | 12, new 3 |
| 1 | 11, new 1 | 11, new 2 | 11, new 3 | 15, new 0 |
| 2 | 10, new 2 | 10, new 3 | 14, new 0 | 14, new 1 |
| 3 | 9, new 3 | 13, new 0 | 13, new 1 | 13, new 2 |

The accounting of the code does not reach this average for every length: in simulation,
back-to-back frames of length 3 modulo 4 alternate gaps of 9 and 13 bytes (10.95 bytes on
average), and frames of length 2 modulo 4 average 11.95 bytes. Lengths 0 and 1 modulo 4 average 12
bytes or more.

The PHY declares `integrated_ifg_inserter`, so the MAC adds no gap of its own. The BASE-R PHYs
reuse its transmitter without declaring it, so the MAC gap adds to its own there ([BASE-R
PHYs](#baser-phys)). With `dic=False` the PHY keeps a gap of 12 bytes or more after each frame,
which costs bandwidth on short frames and saves logic.

**Receive.** An aligner moves a start character found on lane 4 to lane 0. The receiver starts a
frame on a word that holds the start character followed by the preamble. It ends the frame on the
terminate character, whose lane gives the byte enables of the last word.

The receiver does not decode XGMII error characters: its `error` output is always 0, so an
errored frame reaches the MAC, whose FCS check catches it.

With `model=True`, both clock domains run from sys, for the LiteX XGMII simulation model. The
PHY clock module has a `reset` register, but this register does not reset the clock domains.

The PHY pads are this 64-bit, single-edge form. An external XGMII PHY chip uses 32 bits on both
clock edges; the design converts it to the 64-bit form with DDR inputs and outputs, which the PHY
does not instantiate.

## 10BASE-T on FPGA pins {#par-10baset}

**Experimental.** This PHY drives a 10BASE-T link from FPGA pins, with a few resistors and
capacitors instead of an external PHY (`LiteEthPHY10BASET`). It is not an IEEE 802.3 compliant
transceiver: no isolation, no waveform shaping, no collision detection, no jabber control. Use it
at your own risk, on links where these do not matter.

10BASE-T sends Manchester-coded bits at 10 Mbps: each bit has a transition in its middle, rising
for a 1 and falling for a 0 ([PHY interfaces and line coding](#phy-interfaces)). Between frames
the transmitter is silent, except for a Normal Link Pulse every 16 ms that keeps the link up at
the link partner.

### Wiring {#par-10baset-wiring}

The figure below shows the wiring of the bench target `bench/arty_10baset.py`. Two LVCMOS33
outputs drive the transmit pair through 1 µF series capacitors. An LVDS input samples the receive
pair.

Each pair has a 100 Ω termination and two 1 kΩ bias resistors. The bias resistors go to four
more FPGA pins, driven low and high, to keep the whole circuit on one PMOD connector.

![10BASE-T wiring of the Arty bench target on PMOD B: the FPGA transmits on RJ45 pins 3 and 6 and
receives on pins 1 and 2, through a termination, two bias resistors and series capacitors on
each pair. The bias resistors go to FPGA pins held low and high. A transformer may replace the
capacitors.](par_10baset.svg)

The FPGA transmits on RJ45 pins 3 and 6 and receives on pins 1 and 2, like a switch port. It
connects to a computer network card with a straight cable, or to a switch port with automatic
crossover. The receiver detects and corrects an inverted receive pair.

### Operation {#par-10baset-operation}

The PHY runs from one clock domain, `refclk_cd` (`"eth"` by default), at `clk_freq`. The
frequency must be a multiple of 20 MHz, for the half-bit time of the transmitter, and at least
60 MHz, so that the receiver tells a mid-bit transition from a boundary one. A higher frequency
gives the receiver more margin; the bench target uses 100 MHz.

| Block | Function |
|---------------------|-------------------------------------------------------------------------|
| Transmitter | Manchester encoder, end-of-frame idle (TP_IDL), 9.6 µs gap, link pulses |
| Receiver | Transition detection, Manchester decoder, polarity detection on the SFD |
| Link monitor | Link up while frames or link pulses arrive, down after 150 ms without |

The transmitter enforces the 96 bit time gap itself, because the MAC gap inserter counts clock
cycles, not bit times. The receiver follows the clock of the link partner: a transition closer
than 3/4 of a bit to the previous mid-bit one is a boundary transition and is ignored. No
transition for 1.5 bit times ends the burst.

The PHY has a `status` register with two fields, `link_up` and `inverted` (receive pair
polarity). Its pads either drive the pairs directly (`td_p`, `td_n`, `rd_p`, `rd_n`) or connect
to an external driver and comparator (`tx`, `tx_en`, `rx`).

The PHY supports half-duplex without collisions only. A link partner that sees link pulses but no
autonegotiation selects 10 Mbps half-duplex by parallel detection, which matches. The standalone
generator accepts this PHY and leaves the line driver outside the core.

## Simulation model {#par-model}

The simulation PHY connects the MAC to the Ethernet module of the LiteX simulator, which bridges
to a TAP interface of the host (`LiteEthPHYModel`). Both clock domains run from sys. The model
carries frames without preamble or FCS (`with_preamble_crc = False`), because the TAP interface
exchanges bare frames. [Simulation and verification](#simulation) shows how to run it.

## Limitations {#par-limitations}

- **Inter-frame gap on MII and RMII.** The MAC counts the gap in 12 `eth_tx` cycles, not bytes
  on the wire, and the MII, RMII and GMII/MII (in MII mode) PHYs set no `tx_gap_cycles`. The gap
  on the wire is then 48 bit times on MII, 24 bit times on RMII at 100 Mbps and 2.4 bit times on
  RMII at 10 Mbps, instead of 96. A receiver can merge back-to-back frames at 10 Mbps on RMII.
- **RMII speed after reset.** The PHY transmits at 10 Mbps until it has received a frame.
- **Receive errors on MII and GMII.** The MII, GMII and GMII/MII PHYs ignore RX_ER; only the FCS
  check of the MAC catches a corrupted frame.
- **GMII outside Xilinx.** The GMII and GMII/MII clock modules instantiate `BUFG` primitives.
- **RGMII speeds.** Only the ECP5 PHY, with `with_dynamic_link`, runs at 10 and 100 Mbps; the
  other RGMII PHYs run at 1 Gbps.
- **RGMII in the generator.** The generator accepts the 7-Series, UltraScale and ECP5 RGMII PHYs
  only; the GW5, Titanium and Trion PHYs are also not exported by `liteeth.phy`.
- **RGMII delays.** The Agilex PHY ignores `rx_delay`. The 7-Series PHY limits `rx_delay` with a
  300 or 400 MHz IDELAYCTRL reference.
- **XGMII.** No error character decoding; the `reset` register does not reset the clock domains;
  the deficit idle count averages less than 12 bytes for frame lengths of 2 and 3 modulo 4.
- **10BASE-T.** Experimental, half-duplex, no collision handling, no IEEE 802.3 compliance.

## In the code {#par-code}

| File | Content |
|------------------------------------|---------------------------------------------------------|
| `phy/__init__.py` | `LiteEthPHY` autodetection, public classes, module aliases |
| `phy/common.py` | `LiteEthPHYMDIO`, `LiteEthPHYHWReset` |
| `phy/parallel/mii.py` | `LiteEthPHYMII` |
| `phy/parallel/rmii.py` | `LiteEthPHYRMII`, speed detection, timer |
| `phy/parallel/gmii.py` | `LiteEthPHYGMII` |
| `phy/parallel/gmii_mii.py` | `LiteEthPHYGMIIMII`, mode detection |
| `phy/parallel/rgmii/` | One file per family: S6, S7, US, ECP5, GW5, Trion, Titanium, Agilex |
| `phy/parallel/xgmii.py` | `LiteEthPHYXGMII`, deficit idle count, receive aligner |
| `phy/serial/baset.py` | `LiteEthPHY10BASET` |
| `phy/simulation/model.py` | `LiteEthPHYModel` |

The files are in `liteeth/`. The established module names, such as `liteeth.phy.mii` or
`liteeth.phy.s7rgmii`, stay importable through an alias table in `phy/__init__.py`. The tests are
`test/test_rgmii_rx.py`, `test/test_ecp5rgmii.py`, `test/test_agilex_rgmii.py`,
`test/test_xgmii_phy.py`, `test/test_10baset.py` and `test/test_phy_model.py`.

Where to go next: [PHY interfaces and line coding](#phy-interfaces) for the signals,
[MAC](#mac) for what the MAC does with the PHY attributes, [Supported FPGAs and boards](#boards)
for the boards of each PHY, [Debugging](#debugging) when the link does not come up.
