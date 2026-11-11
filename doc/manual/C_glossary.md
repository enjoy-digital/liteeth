# Glossary {#glossary}

The terms below are used throughout the manual. Ethernet terms follow IEEE 802.3 and the network
protocols their RFCs; [Ethernet in a nutshell](#ethernet-primer) and
[ARP, IP, UDP and Etherbone](#network-primer) explain them in context.

| Term | Meaning |
|------------------------|-------------------------------------------------------------------------|
| 1000BASE-X | 1 Gbps Ethernet over a serial link with 8b/10b coding at 1.25 Gbps, used by SFP modules |
| 2500BASE-X | The same coding at 3.125 Gbps, for 2.5 Gbps |
| 5GBASE-R, 10GBASE-R, 25GBASE-R | 5, 10 and 25 Gbps Ethernet over a serial link with 64b/66b coding |
| 8b/10b | Line code that sends each byte as a 10-bit code group, DC balanced, with control code groups |
| 64b/66b | Line code that sends 64 bits as a 66-bit block with a 2-bit sync header, scrambled |
| ARP | Address Resolution Protocol: finds the MAC address of an IPv4 address (RFC 826) |
| Autonegotiation | Exchange by which two link ends agree on the link mode; Clause 37 for 1000BASE-X |
| AXI-Stream | Streaming interface of AMD IP cores; LiteEth adapts it for hardware MACs |
| Beat | One word of a stream: one cycle with `valid` and `ready` high |
| BER | Bit Error Ratio: errored bits over transferred bits; a BASE-R PCS flags a high BER |
| Block lock | State of a BASE-R receiver that has found the 66-bit block boundaries |
| Broadcast | Frame or packet to all stations: MAC `ff:ff:ff:ff:ff:ff`, IPv4 255.255.255.255 |
| CDC | Clock-Domain Crossing: logic that moves data between two clocks, here asynchronous FIFOs |
| CMAC | Hard 100G Ethernet MAC and PCS of the AMD UltraScale+ devices |
| Comma | Code group with a unique bit pattern (K28.5) that marks 8b/10b word boundaries |
| CRC | Cyclic Redundancy Check; Ethernet uses CRC-32 for the FCS |
| Crossbar | LiteEth block that dispatches packets to ports by EtherType, protocol or UDP port |
| CSR | Control and Status Register of a LiteX design, on the CSR bus; *register* in this manual |
| DAC | Direct Attach Copper: a cable with SFP plugs at both ends, without optics |
| DHCP | Dynamic Host Configuration Protocol: gets an IPv4 address from a server (RFC 2131) |
| DIC | Deficit Idle Count: lets the 10G transmitter shorten or lengthen gaps to align starts, keeping a 12-byte average |
| E2E | End-to-end delay mechanism of PTP: the slave measures the delay to the master |
| Etherbone | Protocol carrying Wishbone reads and writes in UDP packets, from the White Rabbit timing project |
| EtherType | 16-bit field of the Ethernet header that names the payload protocol (IPv4, ARP) |
| External PHY | PHY chip on the board between the FPGA and the cable, for MII to RGMII |
| FCS | Frame Check Sequence: the 4-byte CRC-32 at the end of an Ethernet frame |
| FEC | Forward Error Correction; LiteEth BASE-R PHYs run without it |
| Frame | Ethernet unit from the destination MAC address to the FCS |
| GMII | Gigabit Media Independent Interface: 8 bits at 125 MHz between MAC and PHY |
| GTP, GTX, GTH, GTY | Serial transceivers of the AMD 7-Series, UltraScale and UltraScale+ families |
| Hybrid mode | Hardware stack and software MAC sharing one PHY, each with its own addresses |
| ICMP | Internet Control Message Protocol; LiteEth answers echo requests (ping) (RFC 792) |
| IFG | Inter-Frame Gap: idle time between frames, at least 12 bytes (96 bit times) |
| IGMP | Internet Group Management Protocol: joins IPv4 multicast groups (RFC 2236 for IGMPv2) |
| IPv4 | Internet Protocol version 4 (RFC 791) |
| Jumbo frame | Frame with more than 1500 bytes of payload, typically 9000 |
| Link partner | Device at the other end of the cable: switch, NIC or another board |
| LiteScope | Logic analyzer core of LiteX, read over a bridge |
| MAC | Media Access Control: framing, addressing and FCS; `LiteEthMAC` in LiteEth |
| MAC address | 48-bit address of an Ethernet interface; LiteX defaults start with `10:e2:d5` |
| MDI | Medium Dependent Interface: the connector and cable side of a PHY |
| MDIO, MDC | Management bus of an external PHY, data and clock (Clause 22) |
| MII, RMII | Media Independent Interface (4 bits at 25 MHz) and its Reduced form (2 bits at 50 MHz) |
| MTU | Maximum Transmission Unit: largest payload, 1500 bytes; LiteEth's `eth_mtu` is a frame size |
| Multicast | Packet to a group of stations; IPv4 multicast maps to MAC addresses `01:00:5e:xx:xx:xx` |
| Ordered set | Sequence of code groups with a protocol meaning, such as /C/ and /I/ in 8b/10b |
| P2P | Peer-to-peer delay mechanism of PTP: each link measures its own delay |
| Packet | ARP, IP, ICMP, UDP or Etherbone unit carried in a frame |
| PCS | Physical Coding Sublayer: line coding, alignment and autonegotiation |
| PMA | Physical Medium Attachment: serialization and clock recovery, the transceiver here |
| PMD | Physical Medium Dependent: the optics or copper driver, in the SFP module |
| Preamble, SFD | Seven `0x55` bytes and the Start Frame Delimiter `0xd5` before every frame |
| PRBS | Pseudo-Random Binary Sequence; PRBS31 tests a serial channel bit by bit |
| PTP | Precision Time Protocol, IEEE 1588: synchronizes clocks over a network |
| QPLL | Quad PLL of an AMD transceiver quad, shared by its channels |
| RGMII | Reduced GMII: 4 bits on both clock edges at 125 MHz |
| SFP, SFP+, SFP28 | Pluggable module cages for 1 Gbps, 10 Gbps and 25 Gbps links; QSFP28 has four lanes |
| SGMII | Serial GMII: 1000BASE-X coding with its own autonegotiation, to an external PHY |
| Software MAC | The MAC with SRAM slots, driven by a CPU running the BIOS or Linux |
| Store-and-forward | Buffering of a whole frame before passing it on, so that a frame is forwarded complete or dropped whole when the next stage cannot keep up |
| TBI | Ten-Bit Interface: the 10-bit code groups between an 8b/10b PCS and a PMA |
| TFTP | Trivial File Transfer Protocol, used by the BIOS to boot over the network (RFC 1350) |
| TSU | Time-Stamping Unit: the clock of the PTP core, in seconds and nanoseconds |
| TTL | Time To Live: IPv4 hop limit; LiteEth sends 128 |
| UDP | User Datagram Protocol (RFC 768) |
| User port | Port of a LiteEth crossbar given to user logic (`get_port`) |
| Wishbone | On-chip bus of LiteX SoCs |
| XGMII | 10 Gigabit MII: 32 data and 4 control bits on both clock edges; LiteEth uses 64 and 8 on one edge |
