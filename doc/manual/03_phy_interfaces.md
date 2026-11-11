# PHY interfaces and line coding {#phy-interfaces}

This chapter describes how a frame leaves the MAC and reaches the cable. It starts with the parallel
interfaces to an external PHY chip, MII to RGMII, then the serial links that LiteEth drives from
FPGA transceivers: 8b/10b for 1000BASE-X and SGMII, 64b/66b for the BASE-R rates. It ends with
100GBASE-R and with 10BASE-T on FPGA I/Os.

The chapter follows IEEE 802.3 and the industry specifications of RMII, RGMII and SGMII. Each
section ends with the LiteEth PHY that implements the interface; Part III describes these PHYs in
detail. [Ethernet in a nutshell](#ethernet-primer) gives the frame and the sublayers this chapter
builds on.

## The external PHY chip {#iface-external-phy}

Most boards with an RJ45 connector carry a PHY chip between the FPGA and the magnetics. The chip
does what FPGA I/Os cannot: the analog signal processing of 100BASE-TX and 1000BASE-T, and the
autonegotiation of the twisted pair. It talks to the MAC over a parallel interface and is configured
through a two-wire management bus.

The management bus is *MDIO* (IEEE 802.3 Clause 22): a clock, MDC, driven by the MAC side, and a
bidirectional data line, MDIO. Each access names a PHY address and a register number, each 5 bits
wide, and moves 16 bits of data. The table below lists the registers every Clause 22 PHY has.

| Register | Name | Content |
|---------:|---------------------|--------------------------------------------------------------|
| 0 | Control | Reset, loopback, speed, autonegotiation enable and restart |
| 1 | Status | Link status, autonegotiation complete, abilities |
| 2, 3 | PHY identifier | Manufacturer and model |
| 4 | Advertisement | Abilities sent to the link partner |
| 5 | Link partner ability | Abilities received from the link partner |

The registers above 15 are vendor specific: RGMII delays, LED modes, the resolved speed. Many
settings are also *straps*: pins that the chip samples when it leaves reset, such as its MDIO
address or its RGMII delay mode. A reset pulse on the PHY chip reloads them.

In LiteEth, `LiteEthPHYMDIO` (`liteeth/phy/common.py`) exposes MDC, the MDIO output enable and data
as the bits of a register; software runs the protocol bit by bit. The LiteX BIOS uses it in its
`mdio_read`, `mdio_write` and `mdio_dump` commands. `LiteEthPHYHWReset` holds the reset pin of the
chip for 256 clock cycles after power-up.

## MII, RMII and GMII {#iface-parallel}

The parallel interfaces carry the frame a few bits at a time, with a clock and a few control lines.
The transmit side carries data and an enable; the receive side carries data, a valid flag and an
error flag. The preamble and SFD travel on these interfaces like the rest of the frame. The figure
below shows their signals.

![The parallel interfaces between the MAC and an external PHY. MII carries 4 bits per clock, RMII
2 bits with a single shared clock, GMII 8 bits, and RGMII 4 bits on both clock edges. The
arrows show who drives each clock.](iface_parallel.svg)

| Interface | Data | Clock at the top rate | Rates | Clock source |
|--------------|------------|-------------------------|--------------------|------------------------------------|
| MII | 4 bits | 25 MHz | 10, 100 Mbps | PHY drives TX_CLK and RX_CLK |
| RMII | 2 bits | 50 MHz | 10, 100 Mbps | One reference clock shared by both chips |
| GMII | 8 bits | 125 MHz | 1 Gbps | MAC drives GTX_CLK, PHY drives RX_CLK |
| RGMII | 4 bits, DDR | 125 MHz | 10, 100, 1000 Mbps | MAC drives TXC, PHY drives RXC |

*MII* (Clause 22) sends a nibble per clock: TXD[3:0] with TX_EN, RXD[3:0] with RX_DV and RX_ER. The
PHY drives both clocks, at 25 MHz for 100 Mbps and 2.5 MHz for 10 Mbps, so the MAC follows the speed
of the link without knowing it. MII also has CRS and COL for half-duplex.

*RMII* halves the data pins: two bits per clock, at 50 MHz from a reference clock that both the MAC
and the PHY receive. CRS_DV merges carrier sense and data valid.

At 10 Mbps, the clock stays at 50 MHz and each pair of bits is repeated ten times; the MAC must find
the speed from the data or from the PHY registers.

*GMII* (Clause 35) widens the bus to a byte at 125 MHz for 1 Gbps. The MAC drives the transmit
clock, GTX_CLK, and the PHY drives the receive clock. A GMII PHY chip usually also has the MII
transmit clock, for 10 and 100 Mbps links on the same pins.

In LiteEth, `LiteEthPHYMII` and `LiteEthPHYRMII` give the MAC an 8-bit stream in their 25 MHz and
50 MHz clock domains, one byte every few cycles; the RMII PHY detects the speed from the received
data. `LiteEthPHYGMII` runs at 125 MHz, and
`LiteEthPHYGMIIMII` switches to MII when the link runs at 10 or 100 Mbps. See [MII, RMII, GMII,
RGMII, XGMII and 10BASE-T](#parallel-phys).

## RGMII {#iface-rgmii}

*RGMII*, reduced GMII, carries the byte of GMII on 4 data pins by using both clock edges: the low
nibble on the rising edge, the high nibble on the falling edge. TX_CTL and RX_CTL carry the enable
on the rising edge and the enable XOR the error on the falling edge. Twelve pins replace the 22 of
GMII.

The clock and the data change at the same instant at the transmitter. For the receiver to sample the
data in the middle of its eye, the clock must be delayed by about 2 ns, a quarter of the 8 ns
period. The figure below shows the edges and the delay.

![RGMII timing at 1 Gbps. The data and the control signal change on both clock edges; the
receiver samples them with a clock delayed by about 2 ns, which the PHY chip, the board traces or
the FPGA adds.](iface_rgmii_timing.svg)

The delay can come from three places: the PHY chip (an *internal delay* mode, set by a strap or an
MDIO register), longer clock traces on the board, or a delay element in the FPGA. Each direction
needs exactly one delay. Two delays, or none, give a link that comes up but loses frames.

At 10 and 100 Mbps, RGMII runs at 2.5 and 25 MHz and carries one nibble per clock cycle. Between
frames, a PHY may also send its *in-band status* on RXD: link up, speed and duplex, so that the MAC
can follow the link without MDIO.

In LiteEth, each FPGA family has its own RGMII PHY, because DDR I/Os and delay elements differ
between vendors. The `tx_delay` and `rx_delay` parameters default to 2 ns on most families; set
them to zero when the PHY chip or the board adds the delay. The Efinix and Agilex PHYs have fixed
delays ([MII, RMII, GMII, RGMII, XGMII and 10BASE-T](#parallel-phys)).

The ECP5 RGMII PHY reads the in-band status into a register and can follow the link speed ([MII,
RMII, GMII, RGMII, XGMII and 10BASE-T](#parallel-phys)).

## Serial links: PCS and PMA {#iface-serial}

Between an FPGA and an SFP module, and in every LiteEth PHY above 1 Gbps, Ethernet is a serial
line: one differential pair per direction, with the clock embedded in the data.

The PCS turns the frame into a *line code* that guarantees transitions and marks the frame
boundaries. The PMA serializes it and recovers the clock and the bits at the other end. The figure
below shows the chain.

![A serial Ethernet link. The PCS, in the FPGA fabric, encodes the frame; the transceiver
serializes it at the line rate and recovers clock and data on the receive side; the SFP module
converts the signal for the fibre or the cable.](iface_pcs_pma.svg)

The line code costs bandwidth, so the line rate is higher than the data rate. The table below gives
the rates of the serial modes LiteEth implements.

| Mode | Line code | Line rate | Data rate | PCS word clock |
|--------------|-----------|--------------|-----------|--------------------------------|
| 1000BASE-X, SGMII | 8b/10b | 1.25 Gbps | 1 Gbps | 125 MHz, 8 bits |
| 2500BASE-X | 8b/10b | 3.125 Gbps | 2.5 Gbps | 312.5 MHz, 8 bits |
| 5GBASE-R | 64b/66b | 5.15625 Gbps | 5 Gbps | 78.125 MHz, 64 bits |
| 10GBASE-R | 64b/66b | 10.3125 Gbps | 10 Gbps | 156.25 MHz, 64 bits |
| 25GBASE-R | 64b/66b | 25.78125 Gbps | 25 Gbps | 390.625 MHz, 64 bits |

The PCS word clock is the rate at which the PCS handles data words in the fabric; some PHYs run
narrower or wider words with a gearbox, as [1000BASE-X, 2500BASE-X and SGMII](#basex-phys) and [5G,
10G and 25G BASE-R, and 100G](#baser-phys) describe.

The transceiver needs a *reference clock* at a frequency its PLL can multiply to the line rate, such
as 125 MHz or 156.25 MHz. The board provides it, from an oscillator or a clock chip; each PHY lists
the frequencies it accepts.

## 8b/10b coding {#iface-8b10b}

*8b/10b* (Clause 36) maps each byte to a 10-bit *code group*. The code groups are chosen so that the
line never stays at one level for more than five bits, which lets the receiver recover the clock,
and so that the numbers of ones and zeros balance out over time.

A byte maps to a balanced code group, with five ones and five zeros, or to a pair of code groups,
one with two more ones and one with two more zeros. The encoder tracks the *running disparity*, the
sign of the balance so far, and picks, in a pair, the code group that brings it back. A
code group that breaks the disparity rule, or that does not exist, is a coding error.

Besides the 256 data code groups, written D*x.y*, 8b/10b has a few *control* code groups, written
K*x.y*. K28.5 contains a *comma*, a 7-bit pattern that no data code group contains, and that no
sequence of data code groups forms across their boundaries. The receiver searches for it to find
where each code group starts: this is *comma alignment*.

1000BASE-X sends *ordered sets*, short sequences of code groups that start with a control code
group. The table below lists them, and the figure below shows a frame between idles.

| Ordered set | Code groups | Use |
|-------------|--------------------------|--------------------------------------------------------|
| /I1/, /I2/ | K28.5 D5.6, K28.5 D16.2 | Idle between frames; /I1/ corrects the disparity, /I2/ keeps it |
| /C1/, /C2/ | K28.5 D21.5, K28.5 D2.2, then 2 bytes | Configuration: the autonegotiation word |
| /S/ | K27.7 | Start of packet, replaces the first preamble byte |
| /T/ | K29.7 | End of packet |
| /R/ | K23.7 | Carrier extend, after /T/ |
| /V/ | K30.7 | Error propagation |

![A frame on a 1000BASE-X link. Idles (/I/) fill the line; /S/ replaces the first byte of the
preamble, the data code groups follow, and /T/ /R/ end the frame before the idles
resume.](iface_8b10b.svg)

In LiteEth, the BASE-X PCS (`liteeth/phy/serial/basex/pcs.py`) builds and parses the ordered sets in
the fabric. On most families the 8b/10b encoder and decoder are fabric logic too, and the
transceiver only serializes; a 32-bit PCS variant uses the 8b/10b of the transceiver ([1000BASE-X,
2500BASE-X and SGMII](#basex-phys)).

## 1000BASE-X and SGMII {#iface-basex}

*1000BASE-X* is the gigabit Ethernet of fibre and SFP ports: the 8b/10b PCS above at 1.25 Gbps, with
an autonegotiation of duplex and pause. *SGMII* is an industry interface between a MAC and a copper
PHY chip that reuses the same PCS and line rate, to carry 10, 100 and 1000 Mbps on two pairs instead
of GMII.

The two differ in what they negotiate and in how they carry the slow rates. SGMII runs at 1.25 Gbps
whatever the speed of the copper link: at 100 Mbps each byte is repeated 10 times, at 10 Mbps 100
times. This is *rate adaptation by replication*.

*2500BASE-X* is the same PCS at 3.125 Gbps, carrying 2.5 Gbps. Many SFP modules, NICs and switches
support it next to 1000BASE-X.

## Autonegotiation {#iface-autoneg}

On a twisted pair, the PHY chips autonegotiate with bursts of link pulses (Clause 28) and settle on
the best common speed and duplex. LiteEth does not see this: the PHY chip does it alone and reports
the result in its registers. On 1000BASE-X and SGMII, the PCS negotiates with configuration ordered
sets (Clause 37).

Each side sends /C/ ordered sets carrying a 16-bit *configuration word*. Once it has received three
identical words, it sets the acknowledge bit in its own word. When both sides have seen an
acknowledge, they wait for a link timer and switch to idles: the link is up. The figure below shows
the exchange.

![1000BASE-X autonegotiation. Both sides send their abilities in /C/ ordered sets; each one
acknowledges the word it receives; after a further timer, both send idles and the link is
up.](iface_autoneg.svg)

| Bit | 1000BASE-X meaning | SGMII meaning, from the PHY |
|----:|----------------------------------|-------------------------------------|
| 0 | Reserved, 0 | 1: SGMII word |
| 5 | Full duplex | |
| 6 | Half duplex | |
| 7, 8 | Pause abilities | |
| 10, 11 | | Speed: 10, 100 or 1000 Mbps |
| 12 | Remote fault | Full duplex |
| 13 | Remote fault | |
| 14 | Acknowledge | Acknowledge |
| 15 | Next page | Link up |

An SGMII PHY chip sends its word with bit 0 set and the speed of its copper link; the MAC answers
with an acknowledge. A side that restarts sends an empty configuration word for a while, which makes
the other side restart too.

In LiteEth, the BASE-X PCS runs this exchange in an FSM. It detects SGMII from bit 0 of the received
word, or is forced to SGMII or 1000BASE-X by its `sgmii` parameter; it then adapts the rate. The
received word and the link state are readable in a status register on the PHYs that create it
([Status registers](#basex-status)). The 2500BASE-X PHYs run the same exchange at 3.125 Gbps, so a
link partner must run autonegotiation too, or the PCS must be built without it (`with_autoneg`).

## XGMII {#iface-xgmii}

*XGMII* (Clause 46) is the 10 Gbps media independent interface: 32 data bits and 4 control bits per
direction, on both edges of a 156.25 MHz clock. Inside an FPGA it is usually handled as 64 data bits
and 8 control bits on one edge.

Each byte is a *lane*; its control bit says whether it carries data or a control character.

| Character | Value | Use |
|-----------|--------|--------------------------------------------------------|
| /I/ | `0x07` | Idle |
| /S/ | `0xfb` | Start: replaces the first preamble byte, in lane 0 |
| /T/ | `0xfd` | Terminate: the first byte after the FCS |
| /E/ | `0xfe` | Error |
| /Q/ | `0x9c` | Sequence ordered set, used for link fault signaling |

A frame must start in lane 0 of a 32-bit word, so lane 0 or lane 4 of a 64-bit word.

After a frame that does not end on a word boundary, the next start would come too late if the
transmitter always added 12 idle bytes and then waited for lane 0. The figure below shows a frame on
a 64-bit XGMII.

![A frame on a 64-bit XGMII, shortened to four clock cycles. /S/ sits in lane 0 or lane 4; the data
fills the following lanes; /T/ follows the last byte of the FCS, and idles fill the rest of the word
and the gap.](iface_xgmii.svg)

The *deficit idle count* (IEEE 802.3 subclause 46.3.1.4) solves this. The transmitter may shorten a
gap to 9, 10 or 11 bytes to reach the next start lane, and then lengthens later gaps, up to 15
bytes, so that the average stays at 12 bytes or more. The link keeps its full rate without breaking
the gap rule.

In LiteEth, `LiteEthPHYXGMII` connects the MAC to a 64-bit XGMII at 156.25 MHz, the single-edge
form that a hard IP or a simulation model expects; an external chip with the 32-bit DDR form needs
DDR I/Os in between, which the PHY does not provide. It inserts the gap itself with a deficit idle
count, whose accounting does not yet reach the 12-byte average for every frame length ([MII, RMII,
GMII, RGMII, XGMII and 10BASE-T](#parallel-phys) gives the limits). The BASE-R PHYs reuse its
encoding inside the fabric.

## 64b/66b and BASE-R {#iface-64b66b}

The *BASE-R* modes (Clause 49 for 10GBASE-R) code 64 bits of XGMII data at once into a 66-bit
*block*. Two *sync header* bits come first: `01` on the wire for a block of eight data bytes, `10`
for a block that contains control characters.

A control block starts with a *block type* byte that says where the control characters and the data
sit; its control characters shrink to 7 bits.

![A 64b/66b block. A 2-bit sync header precedes 64 bits of payload: eight data bytes, or a block
type byte followed by 56 bits of data and control characters.](iface_64b66b.svg)

| Block type | Content |
|------------|----------------------------------------------------------------|
| `0x1e` | Eight control characters, such as idles |
| `0x78` | Start in lane 0, then seven data bytes |
| `0x33` | Four control characters, then a start in lane 4 and three data bytes |
| `0x87`, `0x99`, `0xaa`, `0xb4`, `0xcc`, `0xd2`, `0xe1`, `0xff` | Terminate after 0 to 7 data bytes, then control characters |
| `0x4b`, `0x2d`, `0x55`, `0x66` | Ordered sets, alone or with a start |

The 64 payload bits are *scrambled*: XORed with a pseudo-random sequence from the polynomial 1 +
x^39 + x^58. The scrambler is *self-synchronizing*: the descrambler uses the received bits as its
state, so it locks without any handshake. The sync header is not scrambled: it is the one place in a
block with a guaranteed transition.

The receiver finds the block boundaries from the sync headers: this is *block lock*. It shifts the
received bits until 64 consecutive headers are valid, and slips again after 16 invalid headers in a
window of 64. A *BER monitor* reports a high error rate when it counts 16 invalid headers in 125 µs
at 10 Gbps.

BASE-R also defines a *PRBS31* test pattern, a pseudo-random sequence the receiver checks bit by
bit, and *link fault* signaling: a receiver that loses its link sends local fault ordered sets, and
the link partner answers with remote fault.

| Mode | Line rate | Clause | Notes |
|-------------|---------------|------------|-----------------------------------------------|
| 5GBASE-R | 5.15625 Gbps | | 10GBASE-R coding at half the rate, on SFP+ modules and NICs that support it |
| 10GBASE-R | 10.3125 Gbps | 49 | SFP+ fibre and DAC |
| 25GBASE-R | 25.78125 Gbps | 107 | SFP28; BER monitor of 97 invalid headers in 2 ms |

In LiteEth, the BASE-R PCS (`liteeth/phy/serial/baser/pcs/`) implements the encoder, decoder,
scrambler, block lock, BER monitor and PRBS31 in the fabric, and exposes block lock, high BER and
PRBS error counts in registers. It handles link fault partially ([5G, 10G and 25G BASE-R, and
100G](#baser-phys)).

## Forward error correction {#iface-fec}

At 25 Gbps and above, some media need *forward error correction* (FEC): redundant bits that let the
receiver repair errors. IEEE 802.3 defines BASE-R FEC (Clause 74) and Reed-Solomon FEC (Clause 108
at 25 Gbps, Clause 91 at 100 Gbps). Both sides must agree on the FEC, usually through
autonegotiation.

LiteEth implements no FEC, and its BASE-R PHYs no autonegotiation. They need a link partner
configured at the same fixed rate with FEC disabled, which NICs and switches allow on short DAC and
optical links. [5G, 10G and 25G BASE-R, and 100G](#baser-phys) gives the settings.

## 100GBASE-R {#iface-100g}

100 Gbps Ethernet runs on four lanes of 25.78125 Gbps. Its PCS (Clause 82) uses the same 64b/66b
blocks, but distributes them over 20 *PCS lanes*, mapped onto the four physical lanes. Each PCS lane
carries an *alignment marker* every 16 383 blocks, which lets the receiver identify the lanes and
remove their skew.

In LiteEth, 100G goes through the CMAC hard block of UltraScale+ devices, which implements the MAC
and the PCS; LiteEth adapts its streams with an AXI-Stream adapter. A Python model of the 100GBASE-R
PCS serves as a reference for tests. This support is in development ([5G, 10G and 25G BASE-R, and
100G](#baser-phys)).

## 10BASE-T {#iface-10baset}

*10BASE-T* (Clause 14) is the one twisted pair mode simple enough for FPGA I/Os. Each bit is
*Manchester* coded: a transition in the middle of every bit time of 100 ns, from high to low for a 0
and from low to high for a 1. The receiver recovers the clock from these transitions.

Between frames, the line is idle. At the end of a frame, the transmitter holds the line high for a
short time, the TP_IDL. Every 16 ms or so, it sends a *normal link pulse* (NLP), a 100 ns pulse that
tells the link partner that the cable is connected.

A link partner that autonegotiates sends bursts of pulses instead. When it receives only normal link
pulses, it falls back to 10 Mbps half-duplex: this is *parallel detection*. The 10BASE-T PHY of
LiteEth relies on it.

In LiteEth, `LiteEthPHY10BASET` drives the transmit pair from two FPGA outputs and samples the
receive pair with a differential input, through a few resistors and capacitors.

The PHY runs from a clock that is a multiple of 20 MHz, 60 MHz at least. It detects no collision and
is not a compliant 802.3 attachment: its source says to use it at your own risk ([MII, RMII, GMII,
RGMII, XGMII and 10BASE-T](#parallel-phys)).

## Where to go next {#iface-next}

[ARP, IP, UDP and Etherbone](#network-primer) covers the packets inside the frame. Part III
describes the PHYs: [MII, RMII, GMII, RGMII, XGMII and 10BASE-T](#parallel-phys), [1000BASE-X,
2500BASE-X and SGMII](#basex-phys) and [5G, 10G and 25G BASE-R, and 100G](#baser-phys).
