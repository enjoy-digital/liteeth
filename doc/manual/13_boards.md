```{=latex}
\part{Use it}
```

```{=html}
<h1 class="part">Part IV. Use it</h1>
```

# Supported FPGAs and boards {#boards}

This chapter lists the FPGA families and the PHYs that LiteEth supports, the boards that run it,
and what a board of your own needs. Read it before choosing hardware, before drawing the Ethernet
part of a schematic, or before buying SFP modules and a network card for the host.

## At a glance {#boards-glance}

LiteEth has PHYs of three kinds. Parallel PHYs drive an external PHY chip through FPGA IOs.
BASE-X and BASE-R PHYs run the line coding in the FPGA and drive a transceiver. The 10BASE-T PHY
drives the cable from FPGA IOs. [Supported PHYs and FPGAs](#intro-phys) gives, for each interface,
the FPGA families that have a PHY class and the hardware status, with the meaning of each status
word. The PHY chapters describe the experimental and in-development PHYs: [MII, RMII, GMII, RGMII,
XGMII and 10BASE-T](#parallel-phys) and [5G, 10G and 25G BASE-R, and 100G](#baser-phys).

The MAC and the hardware stack above the PHY are plain logic and run on every family. The
transceiver PHYs depend on vendor primitives and, for most of them, on LiteICLink for the PLL and
initialization helpers. [1000BASE-X, 2500BASE-X and SGMII](#basex-phys) and [5G, 10G and 25G
BASE-R, and 100G](#baser-phys) describe each family and its reference clocks.

## How a board gets Ethernet {#boards-litex-boards}

Most users meet LiteEth through LiteX-Boards, the collection of LiteX platforms and targets. 93 of
its targets add Ethernet, most of them with RGMII, RMII or MII to an external PHY chip. A target
creates the PHY with the pins of its board, then calls `add_ethernet` or `add_etherbone` of the
LiteX SoC, as [Integration](#integration) describes.

Targets that support Ethernet share the command-line options of the table below. Some targets add
`--eth-phy` (which PHY or which connector) and `--eth-sfp` (which SFP cage).

| Option | Effect |
|-------------------------|----------------------------------------------------------------------|
| `--with-ethernet` | Software MAC for the CPU: BIOS network commands, netboot and Linux |
| `--with-etherbone` | Hardware stack with an Etherbone bus master, no CPU on the path |
| `--eth-ip` | IP address of the board, 192.168.1.50 by default |
| `--remote-ip` | IP address of the TFTP server for netboot, on targets that offer it |
| `--eth-dynamic-ip` | IP address set at run time from the BIOS (`eth_local_ip`) |
| `--eth-dhcp` | IP address from a DHCP server, on targets that offer it |

On targets that accept `--with-ethernet` and `--with-etherbone` together, the two share one PHY
and one MAC in hybrid mode. The hardware stack keeps 192.168.1.50 and the CPU takes its own MAC
and IP addresses, as [Integration](#integration) explains.

The two commands below select a PHY with these options; `python3 -m
litex_boards.targets.<board> --help` lists the options of any target.

```sh
# Tang Mega 138K Pro: rgmii (default), 1000basex or 10gbaser, on SFP 0 or 1.
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=10gbaser --eth-sfp=0 --build --load
# Alibaba XCKU3P: 1000basex (default) or 10gbase-r.
python3 -m litex_boards.targets.alibaba_xcku3p \
    --with-etherbone --eth-phy=10gbase-r --build --load
```

## Bench boards {#boards-bench}

The `bench/` directory of the repository holds standalone targets that test one PHY on one board:
a LiteX SoC with Etherbone, a small SRAM and the LEDs, with no CPU except in the two Artix-7
5GBASE-R targets, which add a VexRiscv and its BIOS. They are the reference designs
of the PHYs and the first thing to run on a new board.

[Examples and targets](#ex-glance) lists them with their board and PHY, and describes each one.
The FPGA follows from the PHY class: Artix-7 for the Arty A7, the AC701 and the Acorn, Kintex-7
for the Genesys2 and the KC705, ECP5 for the ButterStick and the Colorlight, UltraScale(+) for the
others. The Arty A7 uses its RJ45 port (and PMOD B for 10BASE-T), the Genesys2, ButterStick and
Colorlight their RJ45 port, the XCU1525 its QSFP28 cage, the Alibaba XCKU3P its SFP28 cages and
the other boards their SFP cage.

The `alibaba_10g` target selects its rate with `--eth-speed` (`1g`, `5g`, `10g` or `25g`) and its
cage with `--eth-sfp`. The XCU1525 target uses one lane of the QSFP28 cage at 1.25 Gbps. The two
Artix-7 5GBASE-R targets need a reference clock and an SFP setup of their own, described in
[Examples and targets](#examples).

The Gowin GW5AST PHYs have no `bench/` target: the Tang Mega 138K Pro target of LiteX-Boards,
shown above, is their reference. Its SFP cages carry 1000BASE-X with a 100 MHz reference clock and
10GBASE-R with a 125 MHz one, both on quad 1 of the GTR12 transceivers.

### LiteX Acorn Baseboards {#boards-acorn}

The LiteX Acorn Baseboards are open-hardware carrier boards for the Acorn CLE-215+, an Artix-7 board
in an M.2 format. The standard variant, below, has an RJ45 port driven by an ECP5 through RGMII, an
SFP cage on the Artix-7 transceivers and SATA connectors.

This board shows the two families of LiteEth PHYs: a parallel PHY to an external PHY chip, and
a transceiver PHY to an SFP module.

![The LiteX Acorn Baseboard with the Acorn CLE-215+. On the left, the RJ45 port (RGMII, on the
ECP5) and the SFP cage (1000BASE-X, on the Artix-7 GTP transceivers).](acorn_baseboard.jpg){width=75%}

The Mini variant keeps the Acorn, the SFP and SATA connectors, and drops the ECP5: its two SFP cages
are its only Ethernet ports.

The Mini target of LiteX-Boards builds `A7_1000BASEX` on SFP 0 with a 156.25 MHz reference clock
made by a PLL of the FPGA. It shares the GTP common block (QPLL) with SATA, one PLL of it each. The
LiteX-Boards commit that added this sharing records the test below: Etherbone over the SFP answers
`ping` while SATA runs.

```text
Tested successfully with ./litex_acorn_baseboard_mini.py --with-etherbone --with-sata --build --load.

-> Able to ping 192.168.1.50
-> Able to initialize SATA:
litex> sata_init
Initialize SATA...
[...]
Successful.
```

The PTP option (`--with-ptp`) of this target and of the Titanium Ti375 C529 target of LiteX-Boards,
and the two PTP bench targets, pass an argument that `LiteEthPTP` does not accept, so they fail at
elaboration on the revision of this manual ([PTP targets](#ex-ptp)). [Precision Time
Protocol](#ptp) gives a working instantiation.

### A PCIe network card: SPEC-A7 {#boards-spec-a7}

LiteEth also serves as the Ethernet side of network cards. The open-source LiteX-WR-NIC project
builds a 1 Gbps PCIe network card with White Rabbit synchronization on the SPEC-A7, an Artix-7
PCIe board with an SFP cage. Its Ethernet-only designs use `A7_1000BASEX` on the SFP cage, with
LitePCIe on the host side.

![The SPEC-A7 PCIe board: the SFP cage on the left, the Artix-7 in the centre, SMA connectors for
the timing signals.](spec_a7.jpg){width=70%}

## A board of your own {#boards-custom}

A board needs two things for LiteEth: a path to the link partner, and the clocks of that path.
The table below lists what each kind of PHY needs from the board; the PHY chapters give the
details.

| PHY kind | Board needs |
|-------------------|---------------------------------------------------------------------------|
| MII, RMII | External PHY chip; RMII needs a 50 MHz reference shared by FPGA and PHY |
| GMII, RGMII | External PHY chip; RGMII clock skew from the PHY or from FPGA delays |
| Parallel PHYs | MDIO and MDC to the external PHY; its reset pin on the FPGA, optional |
| BASE-X, BASE-R | Transceiver lane to an SFP cage; a reference clock the PHY accepts |
| SFP cage | `TX_DISABLE` held low by the FPGA; module I2C for 5GBASE-R hosts |
| 10BASE-T | Two outputs, one differential input, resistors and capacitors; the bench wiring uses four more IOs for the bias resistors |

The reference clock is the usual trap of the transceiver PHYs. Each class accepts a short list of
frequencies, and a PLL of the fabric can replace a dedicated clock input on some families only.
Check the list of the class in [1000BASE-X, 2500BASE-X and SGMII](#basex-phys) or [5G, 10G and
25G BASE-R, and 100G](#baser-phys) before choosing an oscillator.

The SFP `TX_DISABLE` pin must reach the FPGA or be tied low. Targets drive it from the platform
pin, whose name and polarity vary: the AC701 pin named `sfp_tx_disable_n` is active high, as the
AC701 target notes. A module with its transmitter disabled gives no light and no link.

## SFP modules and the host {#boards-sfp}

The link partner of a BASE-X or BASE-R PHY is a network card, a switch or a media converter with
the same line mode. The table below gives what to use on each side.

| PHY | Module or cable | Host or link partner |
|--------------|--------------------------------------------------------|--------------------------------------|
| 1000BASE-X | 1000BASE-SX or LX optics, 1 Gbps DAC, copper SFP | SFP port, or a switch with SFP cages |
| SGMII | Copper SFP in SGMII mode, external SGMII PHY chip | RJ45 port of the copper module |
| 2500BASE-X | 2500BASE-X optics or copper SFP | 2.5 Gbps SFP port |
| 5GBASE-R | Module with a 5GBASE-R host interface | 5G or multi-rate port |
| 10GBASE-R | SFP+ optics (SR, LR) or DAC | SFP+ port, FEC off |
| 25GBASE-R | SFP28 optics or DAC | SFP28 port, FEC off |

The BASE-X PCS runs the Clause 37 autonegotiation of 1000BASE-X and detects SGMII from the
configuration word of the link partner, so one bitstream accepts both kinds of copper modules.

Above 2.5 Gbps, LiteEth has no autonegotiation, no link training and no FEC. Set the speed of the
host port by hand and turn its FEC off; on Linux, `ethtool --set-fec <interface> encoding off` does
it.

A 5GBASE-R host interface is not 5GBASE-T on the cable and not 5000BASE-X. The Artix-7 5GBASE-R
targets configure the module through its I2C bus at boot, with the code of the BIOS `sfp` command.
Module support depends on the PHY and firmware of the module, not on its label.

On the host, put the port in the subnet of the board, 192.168.1.100/24 with the LiteX defaults,
and raise its MTU when the design uses jumbo frames (`eth_mtu` above 1530). `<interface>` stands
for the name of the host port.

```sh
sudo ip addr add 192.168.1.100/24 dev <interface>
sudo ip link set <interface> mtu 9000
```

## In the code {#boards-code}

The PHY classes are in `liteeth/phy/` (`parallel/`, `serial/basex/`, `serial/baser/`,
`serial/baset.py`), and `liteeth/phy/__init__.py` lists the classes exported under `liteeth.phy`.
The bench targets are in `bench/`; the LiteX-Boards targets are in `litex_boards/targets/` of that
repository.

## Where to go next {#boards-next}

[Getting started](#getting-started) runs the Arty A7 target; [Integration](#integration) adds
LiteEth to a design of your own; [Debugging](#debugging) covers a link that does not come up.
