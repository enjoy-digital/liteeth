```{=latex}
\frontmatter
```

# About this document {.unnumbered}

This manual is the reference of the LiteEth project: an Ethernet MAC, a hardware ARP, IP, ICMP and
UDP stack, an Etherbone frontend, a UDP streamer, a PTP slave clock, and PHYs from 10BASE-T on FPGA
I/Os to 25GBASE-R on FPGA transceivers. It is written for FPGA engineers who need Ethernet in a
design, and it teaches Ethernet and the network protocols along the way.

No prior knowledge of Ethernet is assumed. Part I explains how Ethernet frames travel, what the
interfaces between a MAC and a PHY look like, and how ARP, IP, UDP and Etherbone ride on top.

Each section points at the module that implements its mechanism. Part II then describes the core:
its architecture, the MAC, the IP stack, the frontends and PTP.

Part III describes the PHYs, from the parallel interfaces to the serial BASE-X and BASE-R PHYs.

Part IV is the user guide: the supported boards, the first build, the integration in a design, the
examples, the simulation and the debugging. The appendices hold the register reference, the build
options, the glossary, the performance figures and the maintenance notes of this manual.

| If you want to | Read |
|------------------------------------------|----------------------------------------------|
| Know what the project does and does not | [Introduction](#introduction) |
| Learn how Ethernet works | [Ethernet in a nutshell](#ethernet-primer) |
| Understand MII, RGMII, 8b/10b and 64b/66b | [PHY interfaces and line coding](#phy-interfaces) |
| Learn ARP, IP, UDP and Etherbone | [ARP, IP, UDP and Etherbone](#network-primer) |
| Check that your FPGA or board is supported | [Supported FPGAs and boards](#boards) |
| Ping a first board | [Getting started](#getting-started) |
| Add Ethernet to your own design | [Integration](#integration) |
| Understand a link that does not come up | [Debugging](#debugging) |

The manual follows the repository: every name, number and default is checked against the source
code, and the register appendix is generated from it. The title page gives the date of the build.
When the code and the manual disagree, the code is right and the manual has a bug.

## Conventions {.unnumbered}

- Numbers: `0x` prefix for hexadecimal, `0b` for binary. MAC addresses are written
  `10:e2:d5:00:00:00`, IPv4 addresses `192.168.1.50`.
- Rates: line rates in Mbps and Gbps, such as 1.25 Gbps for 1000BASE-X or 10.3125 Gbps for
  10GBASE-R; clock frequencies in MHz. Throughputs are quoted in the unit the tool or the source
  prints, such as MiB/s or KiB/s, and the text says so. Sizes in bytes, KiB and MiB.
- Interfaces are written in capitals (MII, RMII, GMII, RGMII, SGMII, XGMII) and line modes as IEEE
  802.3 writes them (1000BASE-X, 10GBASE-R). IEEE 802.3 is cited by clause, such as "Clause 36";
  the network protocols by their RFC, such as "RFC 791" for IPv4.
- *The PHY* is a LiteEth PHY class; *the external PHY* is a PHY chip on the board; *the MAC* is
  `LiteEthMAC`; *the core* is the MAC with the ARP, IP, ICMP and UDP layers; *a frontend* is a block
  on top of the core, such as Etherbone or the UDP streamer.
- *A frame* is an Ethernet frame; *a packet* is an ARP, IP, UDP or Etherbone unit carried in a
  frame; *a beat* is one word of a stream.
- *Software MAC* is the MAC driven by a CPU through its SRAM slots; *hardware stack* is the core
  with no CPU on the packet path; *hybrid mode* is both on one PHY.
- *The host* is the PC that talks to the board; *the link partner* is the device at the other end
  of the cable: a switch, a NIC or another board.
- `<target>` stands for the name of a target script of `bench/` without its extension, such as
  `arty` or `kc705`.
- `<config>` stands for a YAML configuration file of the standalone generator `liteeth_gen`, such
  as `examples/udp_s7phyrgmii.yml`.
- Addresses are the LiteX defaults: 192.168.1.50 for the board, 192.168.1.100 for the host.
- Paths are relative to the repository root. Shell commands are given for a Linux host.
- When the text names a source file, it is the file to open to check or extend the fact.
