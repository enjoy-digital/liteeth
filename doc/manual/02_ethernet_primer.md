# Ethernet in a nutshell {#ethernet-primer}

This chapter explains Ethernet to an FPGA engineer who has never read IEEE 802.3. It starts from the
shared cable of the first networks, describes the frame and its addresses, then the gap between
frames and what it costs. It ends with the layers of the standard, the media and where LiteEth cuts
them.

Each section starts with the idea, then gives the mechanism, and ends with the part of LiteEth that
implements it. The next chapter, [PHY interfaces and line coding](#phy-interfaces), covers the
signals between the MAC and the PHY; [ARP, IP, UDP and Etherbone](#network-primer) covers the
protocols carried inside the frames.

The reference is IEEE 802.3, cited by clause. Clauses 3 and 4 define the frame and the MAC; the
physical layers each have their own clauses, listed in [Where LiteEth cuts the
layers](#primer-layers).

## From a shared cable to switched links {#primer-history}

The first Ethernet was a single coaxial cable shared by every station. A station listened before
sending, and when two stations sent at once their signals collided. Both then stopped, waited a
random time and tried again. This is *CSMA/CD*: carrier sense, multiple access, collision detection.

Sharing the medium sets the rules that Ethernet still keeps: a frame starts with a preamble so that
receivers can lock onto it, a frame has a minimum size so that a collision is seen before the frame
ends, and stations leave a gap between frames.

Modern Ethernet is *switched* and *full duplex*: each cable joins two devices only, often a device
and a switch port, with one path in each direction. Collisions cannot happen, and both ends send
whenever they want. The frame format, the minimum size and the gap remain.

LiteEth runs full duplex only: the MAC has no collision detection and no backoff. The one place
where half-duplex matters is the 10BASE-T PHY on FPGA I/Os, which advertises nothing but a link: a
link partner then falls back to 10 Mbps half-duplex, and the PHY works as long as no collision
happens ([MII, RMII, GMII, RGMII, XGMII and 10BASE-T](#parallel-phys)).

## The frame {#primer-frame}

Every piece of data on Ethernet travels in a *frame*. A frame carries two addresses, a type, a
payload and a check sequence. On the wire, a preamble comes before it. The figure below shows the
fields with their sizes in bytes.

![An Ethernet frame on the wire. The preamble and the start frame delimiter (SFD) let the receiver
find the frame; the frame itself runs from the destination address to the frame check sequence
(FCS), 64 to 1518 bytes for a standard frame.](primer_frame.svg)

| Field | Size | Content |
|------------------|---------|----------------------------------------------------------------|
| Preamble | 7 bytes | `0x55` repeated: alternating ones and zeros |
| SFD | 1 byte | `0xd5`, the start frame delimiter: the frame starts after it |
| Destination | 6 bytes | MAC address of the receiver |
| Source | 6 bytes | MAC address of the sender |
| EtherType | 2 bytes | Protocol of the payload, such as `0x0800` for IPv4 |
| Payload | 46 to 1500 bytes | The packet carried, padded with zeros up to 46 bytes |
| FCS | 4 bytes | CRC32 of the frame, from the destination to the end of the payload |

Ethernet sends each byte least significant bit first. The preamble is therefore a pattern of
alternating ones and zeros on the wire, and the SFD ends it with two ones in a row. The receiver
uses the preamble to settle and the SFD to find the first bit of the frame.

The *EtherType* names the protocol of the payload: `0x0800` for IPv4, `0x0806` for ARP. A value of
1500 or less is not a type but a length, in the older IEEE 802.3 format; types start at 1536
(`0x0600`). LiteEth reads the field as a type only.

A frame is at least 64 bytes long, from the destination address to the FCS. A payload shorter than
46 bytes is therefore *padded* with zeros. The receiver cannot tell the padding from the data: the
protocol inside the payload carries its own length, such as the total length of an IPv4 header.

The *frame check sequence* is a CRC32 computed over the frame from the destination address to the
end of the payload, with the polynomial `0x04C11DB7`. The receiver computes it again and drops a
frame whose CRC does not match. The CRC is the only check of the frame: a bad frame is dropped,
never repaired or resent by Ethernet.

In LiteEth, the MAC inserts and checks the preamble and SFD (`liteeth/mac/preamble.py`), pads short
frames to 60 bytes before the FCS (`liteeth/mac/padding.py`), and inserts and checks the CRC
(`liteeth/mac/crc.py`). Received frames with a bad preamble or CRC are marked as errors, so that
the MAC or the layers above drop them; the software MAC also counts them in registers. See
[MAC](#mac).

## MAC addresses {#primer-mac-addresses}

A *MAC address* is 48 bits long and names a network interface. It is written as six bytes in
hexadecimal, such as `10:e2:d5:00:00:00`, the default of a LiteX SoC. The first three bytes are
usually an *OUI*, an identifier the IEEE assigns to a manufacturer; the last three are chosen by the
manufacturer.

Two bits of the first byte change the meaning of an address. The least significant bit is the
*group* bit: 0 for a unicast address, one interface; 1 for a multicast address, a group of
interfaces. The next bit is the *local* bit: 1 marks an address chosen by the network administrator
rather than assigned by a manufacturer.

| Address | Meaning |
|-------------------------------|------------------------------------------------------------|
| `ff:ff:ff:ff:ff:ff` | Broadcast: every interface of the network |
| `01:00:5e:xx:xx:xx` | IPv4 multicast; the low 23 bits come from the IP group address |
| `33:33:xx:xx:xx:xx` | IPv6 multicast |
| Group bit 0 | Unicast: one interface |

A network interface keeps the frames whose destination is its own address, the broadcast address or
a multicast group it has joined, and ignores the others. A switch learns which address sits on which
port and forwards each unicast frame to that port only.

In LiteEth, the hardware stack has one MAC address, a parameter of the core, which it sends as
source address and in its ARP replies.

The hardware stack does not filter received frames by destination MAC address. By default the IP
layer does not filter by destination IP address either (`with_ip_broadcast=True`), so that
broadcast, multicast and DHCP replies get through; with `with_ip_broadcast=False` it keeps only the
packets to its own address. The ARP layer answers only the requests for its own address ([ARP, IP,
ICMP, UDP, DHCP and IGMP](#ip-stack)). In hybrid mode, the MAC
sorts the received frames by destination address between the hardware stack and the CPU
([MAC](#mac)).

## Size limits and jumbo frames {#primer-mtu}

The *MTU*, maximum transmission unit, is the largest payload a frame may carry: 1500 bytes for
standard Ethernet. A standard frame is therefore at most 1518 bytes from the destination address to
the FCS. Many NICs and switches also accept *jumbo frames*, with payloads up to about 9000 bytes,
when both ends are configured for them.

Larger frames cut the overhead per byte and the number of frames per second, which matters at 10
Gbps and above. They also need larger buffers in every device on the path.

In LiteEth, the `eth_mtu` parameter is a frame length, not a payload size: the largest frame from
the destination address to the end of the payload, without the FCS. It is 1530 bytes by default
(`eth_mtu_default`), enough for a standard 1514-byte frame, and 9022 bytes for jumbo frames
(`eth_mtu_jumboframe`). It sizes the buffers of the MAC and the stack; a frame that does not fit a
buffer is dropped where a buffer checks it ([MAC](#mac)).

## The gap between frames {#primer-ifg}

A transmitter must leave a minimum *inter-frame gap* (IFG) between two frames: 96 bit times, that is
12 byte times at any rate. With the 8 bytes of preamble and SFD, every frame costs 20 bytes of wire
time beyond its own length. The figure below shows two frames back to back.

![Frames back to back on the wire. Each frame is preceded by 8 bytes of preamble and SFD and
followed by at least 12 bytes of idle time, the inter-frame gap.](primer_ifg.svg)

The gap gives the receiver time to finish the frame and get ready for the next one. During the gap,
the line carries an idle signal whose form depends on the PHY: nothing on MII and GMII, idle code
groups on 1000BASE-X, idle control blocks on 10GBASE-R ([PHY interfaces and line
coding](#phy-interfaces)).

In LiteEth, the MAC inserts the gap after each frame, counted in clock cycles of the transmit
datapath (`liteeth/mac/gap.py`). Some PHYs insert their own gap, or tell the MAC how many cycles to
use. On MII and RMII, which take several cycles per byte, the gap on the wire is therefore shorter
than 12 bytes; [MAC](#mac) gives the details.

## Line rate and useful throughput {#primer-efficiency}

The rate of an Ethernet link, such as 1 Gbps, is the rate of the frames plus their preamble and gap.
The useful throughput is lower: a frame carries 18 bytes of addresses, type and FCS, and costs 20
more bytes of preamble and gap. Small frames waste most of the wire.

The table below gives, for each rate, the number of frames per second with the smallest and the
largest standard frames, and the UDP payload rate with each.

A minimum frame carries 18 bytes of UDP payload and costs 84 bytes of wire time; a maximum frame
carries 1472 bytes and costs 1538. The values are computed from these sizes, not measured.

| Rate | Frames/s, 64 bytes | UDP payload, 64-byte frames | Frames/s, 1518 bytes | UDP payload, 1518-byte frames |
|----------|-----------------|----------------|----------------|----------------|
| 10 Mbps | 14 881 | 2.1 Mbps | 813 | 9.6 Mbps |
| 100 Mbps | 148 810 | 21.4 Mbps | 8 127 | 95.7 Mbps |
| 1 Gbps | 1 488 095 | 214 Mbps | 81 274 | 957 Mbps |
| 2.5 Gbps | 3 720 238 | 536 Mbps | 203 186 | 2 393 Mbps |
| 5 Gbps | 7 440 476 | 1 071 Mbps | 406 372 | 4 785 Mbps |
| 10 Gbps | 14 880 952 | 2 143 Mbps | 812 744 | 9 571 Mbps |
| 25 Gbps | 37 202 381 | 5 357 Mbps | 2 031 860 | 23 927 Mbps |

The rate is the data rate of the link, before line coding: 1000BASE-X carries 1 Gbps on a 1.25 Gbps
serial line, and 10GBASE-R carries 10 Gbps on 10.3125 Gbps ([PHY interfaces and line
coding](#phy-interfaces)).

A design rarely reaches these figures: the throughput also depends on the clock and the width of its
datapath, on the packets its user logic sends, and on the host. [Performance and
resources](#performance) collects the figures recorded for LiteEth.

## Where LiteEth cuts the layers {#primer-layers}

IEEE 802.3 splits Ethernet into *sublayers*, each with a defined interface to the next. The MAC
builds frames. The *reconciliation sublayer* maps them onto a media independent interface, such as
MII or XGMII. Below it, the physical layer has three sublayers, then the connector.

| Sublayer | Role |
|------------|-------------------------------------------------------------------------------|
| MAC | Builds and checks frames: addresses, padding, FCS, gap |
| RS | Reconciliation: maps the MAC onto the media independent interface |
| xMII | Media independent interface: MII, GMII, XGMII, a parallel bus |
| PCS | Physical coding: line code (4b/5b, 8b/10b, 64b/66b), scrambling, idles |
| PMA | Physical medium attachment: serializes the code and recovers the clock |
| PMD | Physical medium dependent: optical or electrical driver of the medium |
| MDI | Medium dependent interface: the connector, RJ45 or SFP cage |

The figure below shows how LiteEth cuts these sublayers on the two kinds of boards it runs on.

![Where LiteEth cuts the 802.3 sublayers. With an external PHY chip (left), LiteEth implements the
MAC and the media independent interface; the chip does the coding and drives the cable. On a
transceiver (right), LiteEth also implements the PCS; the transceiver serializes and the SFP
module drives the fibre or the cable.](primer_layers.svg)

With an external PHY chip, LiteEth stops at the media independent interface. The PHY chip does the
coding, the signal processing and the autonegotiation of the twisted pair: 100BASE-TX and 1000BASE-T
are far too complex for FPGA I/Os. 10BASE-T is the exception that LiteEth runs on I/Os.

On a transceiver, LiteEth implements the PCS in the fabric: the 8b/10b coding of 1000BASE-X, the
64b/66b coding of 10GBASE-R. The transceiver is the PMA, and the SFP module is the PMD and part of
the MDI.

| Clause | Content | LiteEth |
|--------|------------------------------|--------------------------------------------------|
| 3, 4 | Frame format, MAC | `liteeth/mac/` |
| 14 | 10BASE-T | `liteeth/phy/serial/baset.py` |
| 22 | MII and its management interface (MDIO) | `liteeth/phy/parallel/mii.py`, `liteeth/phy/common.py` |
| 35 | GMII | `liteeth/phy/parallel/gmii.py` |
| 36 | 1000BASE-X PCS and PMA | `liteeth/phy/serial/basex/` |
| 37 | 1000BASE-X autonegotiation | `liteeth/phy/serial/basex/pcs.py` |
| 46 | XGMII | `liteeth/phy/parallel/xgmii.py` |
| 49 | 10GBASE-R PCS | `liteeth/phy/serial/baser/pcs/` |
| 82 | 100GBASE-R PCS | `test/model/pcs_100g.py`, model only |

RMII, RGMII and SGMII are not IEEE 802.3 clauses: they are industry specifications that reduce the
pin count of MII and GMII, described in [PHY interfaces and line coding](#phy-interfaces).

## Media and modules {#primer-media}

Ethernet runs on three kinds of media. *Twisted pair* copper, with an RJ45 connector, carries
10BASE-T, 100BASE-TX and 1000BASE-T and needs a PHY chip. *Fibre* carries 1000BASE-X and 10GBASE-R
through an optical module. A *direct attach copper* cable (DAC) joins two cages with fixed modules
at both ends, for short links inside a rack.

An *SFP* module plugs into a cage on the board and carries one serial lane at up to a few Gbps; SFP+
carries 10 Gbps, SFP28 carries 25 Gbps, and a QSFP28 module carries four lanes for 100 Gbps. The
board connects the lane to an FPGA transceiver and the module to a few control pins.

| SFP signal | Role |
|------------|--------------------------------------------------------------------------|
| TX_DISABLE | Turns the laser off; must be low for the module to transmit |
| LOS | Loss of signal: the module receives no light or signal |
| SCL, SDA | I2C bus to the module identification and diagnostics |

A copper SFP module holds its own 1000BASE-T PHY chip. It talks 1000BASE-X or SGMII to the board,
depending on the module, so the FPGA sees a serial link even with an RJ45 cable plugged in.

Most links negotiate when they come up: twisted pair settles speed and duplex with
*autonegotiation* pulses, 1000BASE-X settles duplex and pause with configuration code groups. Links
at 10 Gbps and above run at a fixed rate.
[PHY interfaces and line coding](#phy-interfaces) describes both mechanisms.

In LiteEth, the board target drives TX_DISABLE and the reference clock. On boards that wire the I2C
bus, the `sfp` command of the LiteX BIOS lists each cage and can set a multi-rate copper module to a
host-side interface such as 10GBASE-R or 1000BASE-X ([Integration](#integration)).

## Where to go next {#primer-next}

[PHY interfaces and line coding](#phy-interfaces) describes the signals between the MAC and the PHY,
then [ARP, IP, UDP and Etherbone](#network-primer) the packets inside the frame; [MAC](#mac)
describes how LiteEth builds and checks frames.
