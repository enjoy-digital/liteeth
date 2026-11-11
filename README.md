```
                                      __   _ __      ______  __
                                     / /  (_) /____ / __/ /_/ /
                                    / /__/ / __/ -_) _// __/ _ \
                                   /____/_/\__/\__/___/\__/_//_/

                                 Copyright 2012-2026 / EnjoyDigital

                             A small footprint and configurable Ethernet core
                                      powered by Migen & LiteX
```

[![](https://github.com/enjoy-digital/liteeth/workflows/ci/badge.svg)](https://github.com/enjoy-digital/liteeth/actions) ![License](https://img.shields.io/badge/License-BSD%202--Clause-orange.svg) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/enjoy-digital/liteeth)


[> Intro
--------
LiteEth provides a small footprint and configurable Ethernet core.

LiteEth is part of LiteX libraries whose aims are to lower entry level of
complex FPGA cores by providing simple, elegant and efficient implementations
of components used in today's SoC such as Ethernet, SATA, PCIe, SDRAM Controller...

Using Migen to describe the HDL allows the core to be highly and easily configurable.

LiteEth can be used as LiteX library or can be integrated with your standard
design flow by generating the verilog rtl that you will use as a standard core.

[> Features
-----------
PHY:
  - 10BASE-T PHY directly on FPGA IOs (experimental, see bench/arty_10baset.py).
  - MII / RMII 100Mbps PHYs.
  - GMII / RGMII / SGMII / 1000BASE-X 1Gbps PHYs.
  - SGMII / 2500BASE-X 3.125Gbps PHYs.
  - SGMII / 1000BASE-X over LVDS SelectIO 1Gbps PHYs (Titanium, UltraScale/UltraScale+).
  - XGMII 10Gbps interface to an external PHY or hard IP.
  - 5/10/25GBASE-R native PHYs on Xilinx 7-Series and UltraScale+ transceivers.
  - 1000BASE-X / 10GBASE-R native PHYs on Gowin GW5AST-138 transceivers.
  - 1000BASE-X native PHY on Cologne Chip GateMate SerDes.
  - 100G hardware MAC adapter for the UltraScale+ CMAC (hardware validation pending).

| PHY                 | ECP5 | Spartan6 | Trion | Titanium | 7-Series | UltraScale | UltraScale+ | Gowin GW5 | Agilex | GateMate |
|---------------------|------|----------|-------|----------|----------|------------|-------------|-----------|--------|----------|
| 10BASE-T            |  X   |    X     |   X   |     X    |     X    |      X     |      X      |     X     |    X   |    X     |
| MII                 |  X   |    X     |   X   |     X    |     X    |      X     |      X      |     X     |    X   |    X     |
| RMII                |  X   |    X     |   X   |     X    |     X    |      X     |      X      |     X     |    X   |    X     |
| GMII                |      |    X     |       |          |     X    |      X     |      X      |           |        |          |
| RGMII               |  X   |    X     |   X   |     X    |     X    |      X     |      X      |     X     |    X   |          |
| SGMII / 1000BASE-X  |      |          |       |          |     X    |      X     |      X      |     X     |        |    X     |
| SGMII / 2500BASE-X  |      |          |       |          |     X    |      X     |      X      |           |        |          |
| LVDS 1000BASE-X     |      |          |       |     X    |          |      X     |      X      |           |        |          |
| XGMII               |  X   |    X     |   X   |     X    |     X    |      X     |      X      |     X     |    X   |    X     |
| 5GBASE-R            |      |          |       |          |     X    |            |      X      |           |        |          |
| 10GBASE-R           |      |          |       |          |     X    |            |      X      |     X     |        |          |
| 25GBASE-R           |      |          |       |          |          |            |      X      |           |        |          |
| 100G (CMAC)         |      |          |       |          |          |            |      X      |           |        |          |

Core:
  - Configurable MAC (HW or SW interface), 8 to 512-bit datapaths.
  - ARP / ICMP / IGMP (joiner) / UDP (HW or SW) / DHCP.
  - PTP (IEEE 1588v2) slave core, Layer 3 (UDP/IPv4) transport.

Frontend:
  - Etherbone (Wishbone over UDP: Slave or Master support).
  - UDP Streaming (streamer and raw ports).
  - AXI-Stream adapter for frame-oriented hardware MACs (e.g. UltraScale+ CMAC).

[> Documentation
----------------
The **Ethernet Core & PHYs manual** is the reference of the project: a nightly
[PDF](https://github.com/enjoy-digital/liteeth/releases/download/manual-nightly/liteeth_manual.pdf)
and [HTML page](https://github.com/enjoy-digital/liteeth/releases/download/manual-nightly/liteeth_manual.html)
are published on the [manual-nightly](https://github.com/enjoy-digital/liteeth/releases/tag/manual-nightly)
release. Sources in [doc/manual](doc/manual/README.md); to build it locally (pandoc and XeLaTeX,
see the README there), outputs in `build/doc/`:
```sh
$ python3 doc/manual/build.py                 # figures, checks, PDF and HTML
$ python3 doc/manual/build.py --check         # links, anchors, figures, tables only
```

- **Understand**: [introduction](doc/manual/01_introduction.md), [Ethernet in a nutshell](doc/manual/02_ethernet_primer.md),
  [PHY interfaces and line coding](doc/manual/03_phy_interfaces.md), [ARP, IP, UDP and Etherbone](doc/manual/04_network_primer.md).
- **The core**: [architecture](doc/manual/05_architecture.md), [MAC](doc/manual/06_mac.md),
  [ARP, IP, ICMP, UDP, DHCP and IGMP](doc/manual/07_ip_stack.md), [frontends](doc/manual/08_frontends.md),
  [PTP](doc/manual/09_ptp.md).
- **The PHYs**: [MII to XGMII and 10BASE-T](doc/manual/10_parallel_phys.md), [1000BASE-X, 2500BASE-X and SGMII](doc/manual/11_basex_phys.md),
  [5G, 10G and 25G BASE-R, and 100G](doc/manual/12_baser_phys.md).
- **Use it**: [supported FPGAs and boards](doc/manual/13_boards.md), [getting started](doc/manual/14_getting_started.md),
  [integration](doc/manual/15_integration.md), [examples and targets](doc/manual/16_examples.md),
  [simulation](doc/manual/17_simulation.md), [debugging](doc/manual/18_debugging.md).
- **Appendices**: [registers](doc/manual/A_registers.md), [build options](doc/manual/B_build_options.md),
  [glossary](doc/manual/C_glossary.md), [performance and resources](doc/manual/D_performance.md),
  [maintaining this manual](doc/manual/E_maintenance.md).

Developer notes (architecture and datapaths):
  - [doc/packet_contracts.md](doc/packet_contracts.md): packet contracts and pipeline ownership.
  - [doc/stream_byte_enables.md](doc/stream_byte_enables.md): byte-enabled packet streams and migration.
  - [doc/high_speed_datapaths.md](doc/high_speed_datapaths.md): high-speed (wide) packet datapaths.
  - [doc/mac_storage.md](doc/mac_storage.md): MAC packet storage bounds.
  - [doc/ipv4_receive.md](doc/ipv4_receive.md): IPv4 receive validation.

PHYs:
  - [doc/phy_layout.md](doc/phy_layout.md): PHY layout and import compatibility.
  - [doc/phy_portability.md](doc/phy_portability.md): transceiver PHY architecture and porting guidance.
  - [doc/baser.md](doc/baser.md): native BASE-R PHYs (5/10/25G).
  - [doc/a7_5g.md](doc/a7_5g.md): Artix-7 5GBASE-R benches.
  - [doc/gw5_1000basex.md](doc/gw5_1000basex.md) / [doc/gw5_10gbaser.md](doc/gw5_10gbaser.md): Gowin GW5 PHYs.
  - [doc/100g.md](doc/100g.md) / [doc/100g_pcs.md](doc/100g_pcs.md): 100G hardware MAC and native 100G PCS reference.

Protocols:
  - [doc/ptp.md](doc/ptp.md): PTP core organization.

Integration:
  - [doc/gen.md](doc/gen.md): standalone core generation (`liteeth_gen`).

[> FPGA Proven
---------------
LiteEth is already used in commercial and open-source designs:
- MiSoC: http://m-labs.hk/gateware.html
- ARTIQ: http://m-labs.hk/artiq/index.html
- HDMI2USB: http://hdmi2usb.tv/home/
- and others commercial designs...

[> Possible improvements
------------------------
- add a DMA interface to the MAC (SRAM slots are the only software interface today)
- add PTP Master / Boundary Clock and Layer 2 (Ethernet) transport
- add AXI-Stream / Avalon-ST user ports to the standalone core
- validate the Artix-7 5GBASE-R and UltraScale+ 100G benches on hardware
- ... See below Support and consulting :)

If you want to support these features, please contact us at florent [AT]
enjoy-digital.fr.

[> Getting started
------------------
1. Install Python 3.9+ and FPGA vendor's development tools.
2. Install LiteX and the cores by following the LiteX's wiki [installation guide](https://github.com/enjoy-digital/litex/wiki/Installation).
3. You can find examples of integration of the core with LiteX in LiteX-Boards and in the examples directory.

[> Standalone core generation
-----------------------------
A standalone Verilog core (MAC on a Wishbone/AXI-Lite bus, or hardware UDP/IP stack with streaming
ports) can be generated from a YAML description:
```sh
$ liteeth_gen examples/wishbone_mii.yml
```
See [doc/gen.md](doc/gen.md) for the flavours, YAML keys and examples.

[> Tests
--------
Unit tests are available in ./test/ (some RTL tests require Icarus Verilog and Verilator).
To run all the unit tests:
```sh
$ python3 -m pytest -v
```

Tests can also be run individually:
```sh
$ python3 -m pytest -v test/test_name.py
```

[> License
----------
LiteEth is released under the very permissive two-clause BSD license. Under
the terms of this license, you are authorized to use LiteEth for closed-source
proprietary designs.
Even though we do not require you to do so, those things are awesome, so please
do them if possible:
 - tell us that you are using LiteEth
 - cite LiteEth in publications related to research it has helped
 - send us feedback and suggestions for improvements
 - send us bug reports when something goes wrong
 - send us the modifications and improvements you have done to LiteEth.

[> Support and consulting
-------------------------
We love open-source hardware and like sharing our designs with others.

LiteEth is developed and maintained by EnjoyDigital.

If you would like to know more about LiteEth or if you are already a happy
user and would like to extend it for your needs, EnjoyDigital can provide standard
commercial support as well as consulting services.

So feel free to contact us, we'd love to work with you! (and eventually shorten
the list of the possible improvements :)

[> Contact
----------
E-mail: florent [AT] enjoy-digital.fr
