# 1000BASE-X, 2500BASE-X and SGMII {#basex-phys}

The BASE-X PHYs do the 8b/10b line coding in the FPGA and drive a transceiver or LVDS pins. On
the other side sits an SFP module, a fibre or copper link partner, or an SGMII PHY chip on the
board.

This chapter describes the PCS that all of them share, the boundary with the transceiver, and the
PHY of each FPGA family.

1000BASE-X runs at 1.25 Gbps and 2500BASE-X at 3.125 Gbps; both carry one byte per 10-bit code
group. SGMII uses the same line coding at 1.25 Gbps, with another autonegotiation word and rate
adaptation for 10 and 100 Mbps. [PHY interfaces and line coding](#phy-interfaces) explains
8b/10b, ordered sets and autonegotiation.

## At a glance {#basex-glance}

| Family | Pins | 1 Gbps | 2.5 Gbps | Reference clock | Status |
|-----------------------|---------------------|--------|----------|------------------------|----------------------|
| Artix-7 | GTP transceiver | Yes | Yes | QPLL of the design | Validated on hardware |
| Kintex-7 | GTX transceiver | Yes | Yes | 200 MHz; 125 MHz at 2.5G | Validated on hardware |
| Virtex-7 | GTH transceiver | Yes | Yes | 200 or 156.25 MHz; 156.25 MHz at 2.5G | Validated on hardware |
| UltraScale | GTH transceiver | Yes | Yes | 200 or 156.25 MHz; 156.25 MHz at 2.5G | Validated on hardware |
| UltraScale+ | GTH or GTY transceiver | Yes | Yes | 200 or 156.25 MHz; 156.25 MHz at 2.5G | Validated on hardware |
| UltraScale(+) | LVDS pins | Yes | No | 625 MHz | Validated on hardware |
| Efinix Titanium | LVDS pins | Yes | No | 200 MHz | Build-tested |
| Gowin GW5AST-138B | GTR12 transceiver | Yes | No | 100 MHz | Validated on hardware |
| Cologne Chip GateMate | SerDes | Yes | No | 100 MHz | Build-tested |

[Supported PHYs and FPGAs](#intro-phys) defines the status words. *Validated on hardware* covers
1000BASE-X, 2500BASE-X and SGMII as a whole; the repository does not record a result per family. The
GateMate PHY runs 1000BASE-X only. On UltraScale LVDS, a free-running local reference, as with an
SFP module, is untested.

The transceiver classes follow one name pattern, family then line mode: `K7_1000BASEX`,
`K7_2500BASEX` and so on, with `USP_GTH_` and `USP_GTY_` prefixes on UltraScale+. The LVDS,
GW5 and GateMate PHYs exist at 1 Gbps only. [In the code](#basex-code) lists every class.

The Artix-7, Kintex-7, UltraScale, UltraScale+ and UltraScale LVDS classes are exported by
`liteeth.phy`. The Virtex-7, Titanium, GW5 and GateMate classes are imported from their modules.
The standalone generator accepts the Artix-7, Kintex-7, UltraScale and UltraScale+ transceiver
classes only, not the UltraScale LVDS class ([Integration](#integration)).

## Structure {#basex-structure}

A BASE-X PHY is a wrapper around two parts. The PCS turns the MAC bytes into 10-bit code groups,
runs autonegotiation and reports the link; it is the same for every family. The PMA moves the
code groups through the transceiver or the LVDS pins, with the clocks, resets and initialization
of one device. The figure below shows the split.

![BASE-X PHY: the wrapper joins the shared PCS (8b/10b, autonegotiation) to a device PMA (the
transceiver or the LVDS I/Os, with their clocks, init and reset), which connects to the SFP module
or the SGMII PHY; the PCS and the PMA exchange one 10-bit code group per clock
cycle.](basex_blocks.svg)

The wrapper exposes the MAC streams (8 bits per cycle, one 32-bit word on GateMate), the
`eth_tx` and `eth_rx` clock domains, `link_up` and the registers. The PCS runs at the code group
rate: 125 MHz at 1.25 Gbps, 312.5 MHz at 3.125 Gbps.

The MAC inserts the preamble, the FCS and the gap as usual; the PCS replaces the first preamble
byte with the start code group.

## The PCS {#basex-pcs}

The PCS (`PCS` in `basex/pcs.py`) has a transmitter, a receiver and a control state machine. The
transmitter encodes each frame between a start and a terminate code group, and sends idles or
configuration words between frames. The receiver decodes the code groups, finds the frames and
the configuration words, and passes the bytes to the MAC.

| Code groups | Ordered set | Use |
|------------------------|-------------|-------------------------------------------|
| K28.5, D5.6 or D16.2 | /I/ | Idle between frames |
| K28.5, D21.5 or D2.2, 2 bytes | /C/ | Configuration word for autonegotiation |
| K27.7 | /S/ | Start of packet, replaces the first preamble byte |
| K29.7 | /T/ | End of packet |
| K23.7 | /R/ | Carrier extend after /T/ |

The PCS has two datapath widths, `dw`. At 8 bits it encodes and decodes 8b/10b in fabric and
exchanges one 10-bit code group per cycle with the PMA.

At 32 bits, it uses the hard 8b/10b coder of the transceiver and exchanges four characters and their
K flags per cycle; this mode supports 1000BASE-X only, without SGMII, and serves the GateMate PHY.

### Autonegotiation {#basex-autoneg}

Before carrying frames, the two ends of a 1000BASE-X or SGMII link exchange 16-bit configuration
words in /C/ ordered sets: their abilities, then an acknowledge (IEEE 802.3 Clause 37). The PCS
runs this exchange in a state machine clocked by `eth_tx`. The figure below shows its states.

![BASE-X autonegotiation state machine: break the link for 10 ms, send the configuration word
until the partner ability is stable, send the acknowledge until the partner acknowledges, send
more acknowledges for a timer, then run. Every state returns to the start on a
failure.](basex_autoneg.svg)

The receiver accepts a configuration word when it has received it nine times in a row, more than
the three of Clause 37. A word of zero means that
the link partner restarts autonegotiation, and the PCS restarts too. In RUNNING, the PCS sets
`link_up` and sends idles and frames instead of configuration words.

In every state after the break, a checker expects a valid /C/ or /I/ ordered set in each 6 ms period
(`check_period`). After a period without one, the PCS restarts autonegotiation and pulses `restart`,
which asks the PMA to reset its receiver.

| Parameter | Default | Role |
|--------------------|------------|-----------------------------------------------------|
| `check_period` | 6 ms | Longest time without a valid /C/ or /I/ |
| `breaklink_time` | 10 ms | Empty configuration words at the start |
| `more_ack_time` | 10 ms | Extra acknowledges, 1000BASE-X |
| `sgmii_ack_time` | 1.6 ms | Extra acknowledges, SGMII |
| `with_autoneg` | True | False for a fixed link without configuration words |

With `with_autoneg=False`, the PCS sends idles from the start and raises `link_up` when the
checker sees valid ordered sets. This serves a link partner with autonegotiation disabled. Without
autonegotiation, `sgmii=None` selects SGMII: the link runs at 1 Gbps and `is_sgmii` reads 1; pass
`sgmii=False` for a 1000BASE-X link. The 2500BASE-X PHYs run the same autonegotiation as
1000BASE-X, so a 2.5 Gbps link partner with autonegotiation disabled needs `with_autoneg=False`.

### SGMII {#basex-sgmii}

SGMII is the same line coding used between a MAC and a copper PHY chip, with another meaning of
the configuration word. The PHY chip announces the link state, speed and duplex of its copper
side; the MAC acknowledges. At 10 and 100 Mbps the line runs at 1.25 Gbps too, and each byte is
repeated 100 or 10 times.

The `sgmii` argument of the PCS selects the flavour. `None`, the default, sends 1000BASE-X
configuration words and switches to SGMII when bit 0 of the partner word is set. `True` starts in
SGMII, for PHY chips that complete autonegotiation only with SGMII words. `False` keeps
1000BASE-X.

| Bit | 1000BASE-X word | SGMII word sent by the MAC side |
|---------|---------------------|--------------------------------------|
| 0 | 0 | 1, SGMII in use |
| 5 | Full duplex | 0 |
| 10, 11 | 0 | Speed: 00 10, 01 100, 10 1000 Mbps |
| 12 | 0 | Full duplex |
| 14 | Acknowledge | Acknowledge |
| 15 | 0 | Link up |

In SGMII mode the PCS takes the speed from the partner word and replicates each transmitted byte
to fill the line; the receiver keeps one byte out of 10 or 100. A partner word without a valid
speed or with the link bit low restarts autonegotiation.

### Status registers {#basex-status}

With `with_csr=True`, the PCS adds a `status` register and a link event. The event fires when the
link has stayed up for one second and when it drops, so that software does not see every bounce
during autonegotiation. The PCS needs the sys frequency for this timer (`sys_clk_freq`).

| Field | Bits | Content |
|----------------|-----------|----------------------------------------------|
| `link_up` | 0 | Autonegotiation complete, link running |
| `is_sgmii` | 1 | SGMII in use |
| `config_reg` | 16 to 31 | Last configuration word of the link partner |

The wrappers create the PCS registers differently. The UltraScale LVDS and GateMate PHYs pass
`with_csr` to the PCS, and the Titanium PHY always creates them. The Artix-7 and GW5 PHYs pass
`pcs_kwargs` to the PCS, so a design adds `with_csr` and `sys_clk_freq` there; the `with_csr` of
the GW5 wrapper adds its own `reset` and `status` registers.

The Kintex-7, Virtex-7, UltraScale and UltraScale+ PHYs build the PCS without registers and take no
PCS arguments; their `with_csr` only adds the PHY `reset` register.

## The PMA boundary {#basex-pma}

The Xilinx transceiver PMAs present a symbol interface to the PCS, without back-pressure. The
same signals connect every Artix-7, Kintex-7, Virtex-7, UltraScale and UltraScale+ PMA, so the
PCS has no family-specific code.

| Signal | Direction | Meaning |
|--------------------|-----------|--------------------------------------------------------|
| `tx_data[9:0]` | Input | One code group per `eth_tx` cycle |
| `rx_data[9:0]` | Output | One code group per `eth_rx` cycle |
| `rx_valid` | Output | Qualifies `rx_data`; always high on these transceivers |
| `align` | Input | Comma alignment request from the PCS |
| `restart` | Input | Receiver restart pulse from the PCS |
| `reset` | Input | PHY reset request |

The PMA owns the `eth_tx` and `eth_rx` domains and their half-rate copies. The transceiver
exchanges 20 bits at half the code group rate, and a gearbox converts between 10 and 20 bits
(`PCSGearbox`). The PMA also holds the transceiver initialization sequences and its reset.

The GW5 and LVDS PMAs keep their own interfaces: GW5 qualifies received symbols with `rx_valid`,
and the LVDS PMAs recover the clock themselves. The wrapper of each one connects it to the PCS.

## Xilinx transceivers {#basex-xilinx}

The transceiver wrappers take the reference clock (a pad pair, or a clock signal), the data pads
and the sys frequency. Each class checks its reference frequency against the line rate: a channel
PLL cannot reach 3.125 Gbps from every reference.

| Family | Wrapper | 1 Gbps reference | 2.5 Gbps reference |
|--------------------|--------------------------|----------------------|----------------------|
| Kintex-7 GTX | `k7_gtx.py` | 200 MHz | 125 MHz |
| Virtex-7 GTH | `v7_gth.py` | 200 or 156.25 MHz | 156.25 MHz |
| UltraScale GTH | `ku_gth.py` | 200 or 156.25 MHz | 156.25 MHz |
| UltraScale+ GTH | `usp_gth.py` | 200 or 156.25 MHz | 156.25 MHz |
| UltraScale+ GTY | `usp_gty.py` | 200 or 156.25 MHz | 156.25 MHz |

At 1 Gbps the default reference is 200 MHz; at 2.5 Gbps each class accepts one reference, its
default. The UltraScale+ wrappers also take `refclk_from_fabric`, for a reference that comes from
the fabric instead of a dedicated clock pin. All take `rx_polarity` and `tx_polarity` to invert a
lane.

**Artix-7 GTP.** The Artix-7 PHY does not create its PLL. The design instantiates the GTP common
block with the `QPLL` helper of `serial/gtp_7series.py` and passes one of its two PLLs as
`qpll_channel`. This lets a 1000BASE-X link and another protocol, such as SATA, share the GTP
common block.

| Line rate | Reference | `fbdiv` | `fbdiv_45` | PLL VCO |
|-------------|--------------|---------|------------|------------|
| 1.25 Gbps | 125 MHz | 4 | 5 | 2.5 GHz |
| 1.25 Gbps | 156.25 MHz | 4 | 4 | 2.5 GHz |
| 3.125 Gbps | 125 MHz | 5 | 5 | 3.125 GHz |
| 3.125 Gbps | 156.25 MHz | 5 | 4 | 3.125 GHz |

The table gives the PLL settings that the standalone generator uses, with `refclk_div` 1. The
Artix-7 PHY also takes `with_pcs_buffers`, which adds a register stage on both MAC streams to ease
timing at 312.5 MHz, and it creates its `GTPE2_CHANNEL` at finalization. A target may therefore
change the channel parameters, `gtp_params`, after construction.

The LiteX Acorn Baseboard Mini, below, carries an Artix-7 module and two SFP cages. Its LiteX-Boards
target can combine Etherbone over 1000BASE-X on an SFP cage with SATA, the two sharing the GTP
common block.

![LiteX Acorn Baseboard Mini with an Acorn CLE-215+ Artix-7 module: two SFP cages driven by the
GTP transceivers.](acorn_baseboard_mini.jpg){width=60%}

A LiteX-Boards commit records the test that validated this combination, trimmed:

```text
Tested successfully with ./litex_acorn_baseboard_mini.py --with-etherbone --with-sata --build --load.

-> Able to ping 192.168.1.50
[...]
```

## UltraScale LVDS {#basex-us-lvds}

On UltraScale and UltraScale+, the 1000BASE-X PHY can also run on ordinary differential pins,
without a transceiver (`US_LVDS_1000BASEX`). Its main use is an SGMII PHY chip that provides a
625 MHz clock locked to its own line. The transmitter is an 8:1 `OSERDESE3`; the receiver
recovers the clock with an Alexander phase detector.

The figure below shows the receiver. A data `ISERDESE3` samples the line, and a monitor
`ISERDESE3` samples it half a bit later through an `IDELAYE3`. On each data transition, the
monitor sample tells whether the sampling clock is early or late. Votes accumulate, and a step
request moves the fine phase of the receive MMCM.

![UltraScale LVDS receiver: data and monitor ISERDESE3 half a bit apart feed a phase detector,
which steps the fine phase of the MMCM that clocks both deserializers; the data goes through a
gearbox and a comma aligner to the PCS.](basex_lvds.svg)

The comma aligner runs continuously. A comma cannot appear at another bit position in a valid
8b/10b stream, so realigning on each comma heals a bit slip after a phase excursion. With a
reference that is not frequency-locked to the partner, the phase wraps once per bit time of
drift; this case, as with an SFP module, is untested.

| Requirement | Value |
|---------------------------|----------------------------------------------------------|
| Reference clock | 625 MHz, with a period constraint from the platform |
| UltraScale device | Speed grade -2 or -3, pins in an HR or HP bank |
| UltraScale+ device | `usp=True`, any speed grade, HP bank |
| IDELAYCTRL | Fed at `iodelay_clk_freq`, 300 MHz or more on UltraScale+, held in reset until the PHY MMCM locks |

The PHY has no reset register. A free-running power-on counter sequences the MMCM, never the sys
reset, because resetting the MMCM stops the delay clocks and would reset the SoC through the
IDELAYCTRL. The counter also resets the MMCM after it stays unlocked for 10 ms.

| Register | Fields |
|----------------|-----------------------------------------------------------------------|
| `cdr_control` | `enable` (loop on), `invert` (step direction), `inc`, `dec` (manual steps) |
| `cdr_status` | `position` (phase step modulo 56), `inc_count`, `dec_count` |

The PCS registers are present too, and `pcs_kwargs` reaches the PCS: `sgmii=True` serves a PHY
chip that completes autonegotiation only with SGMII words. When the pads have MDIO pins, the PHY
adds the MDIO registers of [MII, RMII, GMII, RGMII, XGMII and 10BASE-T](#par-mdio).

## Efinix Titanium LVDS {#basex-titanium}

The Titanium PHY runs 1000BASE-X on LVDS pins of Efinix Titanium and Topaz FPGAs
(`EfinixTitaniumLVDS_1000BASEX`). A clocking block derives a 625 MHz serial clock and a 125 MHz
parallel clock from the reference, 200 MHz by default. The transmitter is an LVDS serializer.

The receiver oversamples. Its pads hold four LVDS input pairs, deserialized with static delays
eight taps apart. Majority votes between neighbouring samples correct the bits and choose the
sampling phase; a buffer with a comma aligner rebuilds the 10-bit code groups for the PCS.

The PHY always creates the PCS registers and exposes the PCS link event. When the pads have
`scl` and `sda`, it also adds a LiteI2C master on these pins (`with_i2c`).

## Gowin GW5 {#basex-gw5}

The GW5 PHY connects the PCS to a `GTR12_QUAD` transceiver of the GW5AST-138B, through its raw
10-bit interface at 125 MHz (`GW5_1000BASEX`). It supports Q1 lane 0 or 1, selected with `lane`,
with a 100 MHz differential reference on Q1 REFCLK1. These lanes are the SFP-0 and SFP-1 cages of
the Sipeed Tang Mega 138K Pro dock.

The transceiver does the comma alignment. Registers at the raw interface and buffers at the MAC
interface keep the 125 MHz paths short. Serial and reference clock signals use dedicated pins
selected by the transceiver configuration, so they need no fabric pin constraints. The board
target enables the SFP transmitter and provides the reference clock.

| Register | Content |
|----------|--------------------------------------------------------------------------------|
| `reset` | Resets the lane and the PCS |
| `status` | Bits 0 to 3: transmit PLL lock, receive CDR lock, comma alignment, PCS link up |

**Transceiver configuration.** The transceiver is configured at FPGA configuration time with
hardware-validated settings: register writes generated by the Gowin tools, 1.9.12 for this PHY.
`basex/pma/gw5_config.py` embeds these writes and their TOML source. During the Gowin build, the
project script writes `gw5_1000basex.csr` in the gateware directory and loads it with `set_csr`.

To change the setup, a developer exports the embedded TOML for the lane and regenerates the
writes with the Gowin generator, then updates the vendor configuration checksums of the tests.
The configuration owns the whole quad, so the board target rejects 1000BASE-X together with
PCIe.

**Raw interface.** `GW5SerDes` exposes the configured transceiver without the PCS, for another
PCS such as White Rabbit. The caller registers `tx_data` on the transmit clock and samples
`rx_data` on the receive clock, both 125 MHz. It also supplies the clock domains, resets and
constraints. This interface keeps the hard comma aligner and receive FIFO, so its latency is not
deterministic.

On the Tang Mega 138K Pro, the LiteX-Boards target selects this PHY. The command below builds and
loads Etherbone over SFP-0; `--eth-sfp=1` selects SFP-1. Success is a reply to `ping`.

```sh
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=1000basex --eth-ip=192.168.1.50 --build --load
ping 192.168.1.50
```

The dock must keep its 100 MHz reference clock setting on PLL0. The same board runs 10GBASE-R on
the same cages ([GW5 10GBASE-R](#baser-gw5)).

## Cologne Chip GateMate {#basex-gatemate}

The GateMate PHY runs 1000BASE-X on the SerDes of the Cologne Chip GateMate FPGA
(`GateMate_1000BASEX`). It uses the 32-bit PCS with the hard 8b/10b coder of the SerDes, so its
MAC streams are 32 bits wide at 31.25 MHz, and it supports 1000BASE-X without SGMII. The reference
clock is 100 MHz by default.

Register stages on both MAC streams are on by default (`with_pcs_buffers`). With `with_csr`, the
PHY has a `reset` register, the PCS registers and a status register of the PMA. Some transceiver
parameters of the PMA are marked for checking in the code.

## Limitations {#basex-limitations}

- **SGMII at 2.5 Gbps.** The SGMII flavour and its rate adaptation apply to 1.25 Gbps links; the
  32-bit PCS has no SGMII at all.
- **Kintex-7, Virtex-7, UltraScale and UltraScale+ wrappers.** They take no PCS arguments: no
  PCS `status` register, no forced SGMII, no `with_autoneg=False`, so a 2500BASE-X partner without
  autonegotiation cannot link. A design can call `phy.pcs.add_csr(sys_clk_freq)` after
  construction to get the `status` register.
- **Fixed link and SGMII.** With `with_autoneg=False` and the default `sgmii=None`, the PCS runs
  SGMII and `is_sgmii` reads 1.
- **UltraScale LVDS.** No reset register; a free-running local reference is untested.
- **GW5.** One lane per PHY instance, GW5AST-138B Q1 only, no transceiver sharing with PCIe, no
  deterministic latency on the raw interface.
- **Titanium and GateMate.** Build-tested only.
- **Generator.** The generator accepts the Artix-7, Kintex-7, UltraScale and UltraScale+
  transceiver classes only. The Virtex-7, Titanium, GW5 and GateMate classes are also not exported
  by `liteeth.phy`.

## In the code {#basex-code}

| File | Content |
|--------------------------------------------------|------------------------------------------------|
| `pcs.py` | `PCS`, SGMII timer, transmitter, receiver |
| `pcs32.py` | 32-bit transmitter and receiver for hard 8b/10b |
| `pma/gearbox.py` | `PCSGearbox`, 10 to 20 bits |
| `pma/gt*.py` | Transceiver PMAs: GTP, GTX, 7-Series GTH, UltraScale GTH, UltraScale+ GTH and GTY |
| `pma/us_lvds.py` | UltraScale LVDS clocking, serializers, phase detector |
| `pma/titanium_lvds.py` | Titanium LVDS serializers, clock recovery |
| `pma/gw5.py` | `GW5SerDes` |
| `pma/gw5_config.py` | Embedded GW5 configuration writes and TOML |
| `pma/serdes_gatemate.py` | GateMate SerDes PMA |
| `wrappers/a7_gtp.py` | `A7_1000BASEX`, `A7_2500BASEX` |
| `wrappers/k7_gtx.py` | `K7_1000BASEX`, `K7_2500BASEX` |
| `wrappers/v7_gth.py` | `V7_1000BASEX`, `V7_2500BASEX` |
| `wrappers/ku_gth.py` | `KU_1000BASEX`, `KU_2500BASEX` |
| `wrappers/usp_gth.py` | `USP_GTH_1000BASEX`, `USP_GTH_2500BASEX` |
| `wrappers/usp_gty.py` | `USP_GTY_1000BASEX`, `USP_GTY_2500BASEX` |
| `wrappers/us_lvds.py` | `US_LVDS_1000BASEX` |
| `wrappers/titanium_lvds.py` | `EfinixTitaniumLVDS_1000BASEX` |
| `wrappers/gw5.py` | `GW5_1000BASEX` |
| `wrappers/serdes_gatemate.py` | `GateMate_1000BASEX` |

The files are in `liteeth/phy/serial/basex/`, and the GTP common block helper is
`serial/gtp_7series.py`. The established module names, such as `liteeth.phy.k7_1000basex`, stay
importable. The tests include `test/test_pcs_1000basex.py`, `test/test_basex_boundary.py`,
`test/test_us_lvds_1000basex.py`, `test/test_gw5_1000basex.py` and
`test/test_gatemate_1000basex.py`.

Where to go next: [PHY interfaces and line coding](#phy-interfaces) for 8b/10b and
autonegotiation, [5G, 10G and 25G BASE-R, and 100G](#baser-phys) for the faster PHYs,
[Integration](#integration) for porting a transceiver PHY, [Debugging](#debugging) when the link
does not come up.
