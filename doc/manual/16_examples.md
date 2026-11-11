# Examples and targets {#examples}

This chapter describes the designs shipped with LiteEth: the generator examples of `examples/`,
which produce standalone Verilog cores, and the bench targets of `bench/`, which build complete
FPGA designs for a board. Use them to check a board, to measure a link, or as a starting point for
your own design. [Getting started](#getting-started) walks through a first target step by step.

## At a glance {#ex-glance}

An example is a YAML file given to the generator, `liteeth_gen`. It describes one PHY, one core
flavour and its ports; the generator writes the Verilog of the core and never runs an FPGA
toolchain. [Integration](#integration) explains the flavours and the generated ports, and
[Build options](#build-options) lists every key.

| Example | PHY | Core | What it shows |
|----------------------------------|----------------------------|-------------|--------------------------------|
| `wishbone_mii` | MII | Wishbone | Software MAC on a Wishbone slave |
| `axi-lite-mii` | MII | AXI-Lite | Software MAC on an AXI-Lite slave |
| `udp_s7phyrgmii` | 7-Series RGMII | UDP | Static and dynamic streamer ports |
| `udp_raw_ecp5rgmii` | ECP5 RGMII | UDP | Raw and streamer ports, open toolchain |
| `udp_10baset` | 10BASE-T on IOs | UDP | 10BASE-T from the core clock |
| `udp_a7_gtp_sgmii` | Artix-7 GTP 1000BASE-X | UDP | DHCP client, 32-bit core at 25 MHz (0.8 Gbps) |
| `udp_usp_gth_sgmii` | UltraScale+ GTH 1000BASE-X | UDP | Same on UltraScale+ |
| `udp_xgmii_jumbo` | XGMII | UDP | 10G, jumbo frames, Etherbone |
| `udp_baser` | UltraScale+ GTY 10GBASE-R | UDP | Native 10GBASE-R, jumbo frames, Etherbone |
| `udp_baser_25g` | UltraScale+ GTY 25GBASE-R | UDP | 256-bit core at 200 MHz |

A target is a Python script that builds a LiteX SoC for one board with LiteX-Boards. Most targets
are Etherbone designs: the host reads and writes the bus of the FPGA over UDP, with no CPU in
the FPGA. The PTP and Artix-7 5G targets add a function on top.

| Target | Board | PHY | Function |
|---------------------------------------------|------------------|--------------------|-----------------|
| `arty` | Arty A7 | MII | Etherbone, UDP streamer |
| `arty_10baset` | Arty A7, PMOD B | 10BASE-T on IOs | Etherbone |
| `arty_ptp` | Arty A7 | MII | PTP slave, Etherbone |
| `genesys2` | Genesys2 | 7-Series RGMII | Etherbone |
| `butterstick` | ButterStick | ECP5 RGMII | Etherbone, LiteScope |
| `colorlight_5a_75b` | Colorlight 5A-75B | ECP5 RGMII | Etherbone |
| `kc705` | KC705 | Kintex-7 GTX 1000BASE-X | Etherbone |
| `kcu105` | KCU105 | UltraScale GTH 1000BASE-X | Etherbone |
| `xcu1525` | XCU1525 | UltraScale+ GTY 1000BASE-X | Etherbone |
| `xu8_st1` | Mercury XU8 on ST1 | UltraScale+ GTH 1000BASE-X | Etherbone |
| `acorn_baseboard_mini_ptp` | Acorn Baseboard Mini | Artix-7 GTP 1000BASE-X | PTP slave, Etherbone |
| `alibaba_10g` | Alibaba XCKU3P | UltraScale+ GTY, 1G to 25G | 64-bit Etherbone |
| `ac701_5g` | AC701 | Artix-7 GTP 5GBASE-R | 32-bit Etherbone, CPU |
| `acorn_baseboard_mini_5g` | Acorn Baseboard Mini | Artix-7 GTP 5GBASE-R | 32-bit Etherbone, CPU |

The bench also holds the simulation targets (`sim`, `dhcp_tap`, `mac_100g`), described in
[Simulation and verification](#simulation), the 100G build scripts of [100G benches](#ex-100g),
and the host tools of [Host tools](#ex-tools). The supported FPGA families and the hardware status
of each PHY are in [Supported PHYs and FPGAs](#intro-phys).

## Generating a core {#ex-generate}

The generator reads one example and writes its outputs in `build/` by default. It never calls the
FPGA toolchain: the Verilog, the constraints and the register map are inputs for your own project.

```sh
liteeth_gen examples/udp_s7phyrgmii.yml
# Same, in another directory and under another module name.
liteeth_gen examples/udp_baser.yml --output-dir build/baser --name eth_baser
```

Look for `build/gateware/liteeth_core.v` (or `<name>.v`), the toolchain constraints next to it,
and `build/csr.csv`. The ports of the Verilog module follow the example: one group of signals per
UDP port, and `mac_address` and `ip_address` inputs when the example does not fix the addresses.

## MAC cores {#ex-mac}

`wishbone_mii` and `axi-lite-mii` build the MAC with its SRAM slots behind a bus slave, for a CPU
outside the core: the software MAC of [MAC](#mac). Both use the MII PHY with a 100 MHz `sys` clock
and a big-endian bus; `axi-lite-mii` replaces the Wishbone slave with an AXI-Lite one. The `soc` key
moves the `ethmac` region to `0x50000000`.

The core has an `interrupt` output and the MAC registers of [Register reference](#reg-ethmac). Its
memory map is the one of the `ethmac` of a LiteX SoC, so a driver written for LiteX, such as the
Linux `liteeth` driver, sees the same slots and registers.

## UDP cores with streamer ports {#ex-udp}

`udp_s7phyrgmii` builds the hardware stack on a 7-Series RGMII PHY, with an 8-bit core at
125 MHz. It has two 32-bit streamer ports that show the two ways of addressing a port:

- `udp0` fixes its UDP port (2000) and its destination IP address in the example: the core has
  no address inputs for it.
- `udp1` gives neither: the core gets `udp1_udp_port` and `udp1_ip_address` inputs, and the user
  logic sets them at run time.

`udp_10baset` builds the same stack on the 10BASE-T PHY. The PHY runs from the core clock, which
must be a multiple of 20 MHz and at least 60 MHz (100 MHz in the example). The user logic drives
the transmit pair from `baset_tx` and `baset_tx_en` and feeds the receive pair to `baset_rx`, as
the comment of the example describes. This PHY is experimental: see [Parallel PHYs](#parallel-phys).

## Raw and streamer ports {#ex-raw}

`udp_raw_ecp5rgmii` builds an ECP5 RGMII core with the open toolchain (`trellis`), with one raw
port and two streamer ports. A raw port is the full UDP user port: the user logic gives the
destination IP address, the ports and the length of every packet, and sends it in one burst. A
streamer port adds a FIFO and the packetization, at the price of the control of packet sizes.

The two streamers of this example set their port with a `port:` key, which the generator does not
read; it reads `udp_port`. Both streamers therefore get dynamic `udp_port` and `ip_address`
inputs. Use `udp_port` and `ip_address` in your own configuration to fix them.

## Transceiver cores with DHCP {#ex-dhcp}

`udp_a7_gtp_sgmii` and `udp_usp_gth_sgmii` build a 1000BASE-X core on an Artix-7 GTP and an
UltraScale+ GTH with a 156.25 MHz reference clock. The core is 32 bits wide and runs at 25 MHz in
`sys`, so the generator puts the whole datapath in `sys` (`with_sys_datapath`). Its capacity,
0.8 Gbps, is below the 1 Gbps line rate: sustained full-rate traffic overflows the receive path.
Raise `clk_freq` to 31.25 MHz at least, 40 MHz or more for margin, for full rate. The streamer port
`udp0` has 1024-word FIFOs and dynamic addresses.

`dhcp: True` adds the DHCP client: the core gets a `dhcp` group of signals (`start`, `done`,
`timeout`, `ip_address`), and the user logic copies the leased address to the `ip_address`
input. The generator does not connect the MAC address of the client, so its requests carry an
all-zero client hardware address. [ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack) describes the
client; the user logic renews the lease.

## 10G and 25G cores {#ex-10g}

`udp_xgmii_jumbo` and `udp_baser` build a 64-bit core at 156.25 MHz with jumbo frames
(`eth_mtu: 9022`), Etherbone on UDP port 1234 and one 64-bit streamer port. The first uses the
XGMII PHY, for an external 10G PHY or a hard PCS; the second the native 10GBASE-R PHY on an
UltraScale+ GTY, with a 156.25 MHz reference clock. The Etherbone master is an AXI-Lite `mmap`
interface of the core.

The streamer port keeps a whole jumbo packet in its 2048-word FIFOs and splits packets not closed
by `sink_last` at 8972 bytes, rounded down to 8968 bytes (whole 64-bit words). The `sink_keep`
and `source_keep` signals carry the byte enables of the last word; drive all ones for full words,
or set `with_tkeep: False` to remove them.

`udp_baser_25g` builds the native 25GBASE-R PHY with a 256-bit core at 200 MHz and a
161.1328125 MHz reference clock. The PHY is 64 bits wide at 390.625 MHz; the MAC converts between
the two widths. The link partner must run without FEC, as `phy_fec: none` states: see
[BASE-R PHYs](#baser-phys).

## Etherbone targets {#ex-etherbone}

The Etherbone targets share one structure: a CRG, the PHY, `add_etherbone` with a 255-word buffer
(except `xu8_st1` and `arty_10baset`), a 4 KiB SRAM at `0x20000000` and a LED chaser. They have no
CPU. The SRAM gives the host tools a memory to test, and the identifier string names the build.

```sh
# Build and load the bitstream (volatile), from the repository root.
./bench/kc705.py --build --load
ping 192.168.1.50
litex_server --udp --udp-ip 192.168.1.50 &
./bench/test_etherbone.py --ident --sram --speed
```

`--speed` sends records of 255 words. The Etherbone frontend drops a record longer than its buffer
whole, so on `xu8_st1` (4 words) and `arty_10baset` (16 words) the writes of `--speed` are lost
silently and its reads time out: use `--ident` and `--sram` there.

All targets use the LiteX default addresses: 192.168.1.50 for the board and 192.168.1.100 for the
host. A target writes `csr.csv` in the directory it runs from, and `test_etherbone.py` reads it
from there. Without `--build`, a target elaborates the design and writes the sources without
calling the toolchain, except `alibaba_10g`, which writes nothing without `--build`.

| Target | `sys` clock | Notes |
|------------------------------|-------------|--------------------------------------------------|
| `arty` | 50 MHz | UDP streamer on port 6000, see below |
| `genesys2` | 50 MHz | 7-Series RGMII PHY with its default delays |
| `butterstick` | 50 MHz | `tx_delay=0`, JTAGBone, LiteScope probes on the core |
| `colorlight_5a_75b` | 50 MHz | `tx_delay=0` |
| `kc705`, `kcu105` | 125 MHz | SFP 0, 200 MHz transceiver reference from the CRG |
| `xcu1525` | 125 MHz | Lane of QSFP 0, 200 MHz reference from the CRG |
| `xu8_st1` | 125 MHz | SFP of the ST1 baseboard, buffer of 4 words |

The `butterstick` target does not elaborate on this revision: its LiteScope probe list names a
state machine that the Etherbone packet sender does not have. Remove that probe, or the
analyzer, to build it.

### `arty`: UDP streamer {#ex-arty}

`arty` adds a UDP streamer on port 6000 to the Etherbone design. Each byte it receives sets the
LEDs for one second, then the LED chaser takes them back. The streamer sends to 192.168.1.100,
port 6000, but the code that would send the switches is disabled (`if False`), so `--switches`
receives nothing from this target.

```sh
./bench/test_udp_streamer.py --leds
```

### `arty_10baset`: 10BASE-T on a PMOD {#ex-10baset}

`arty_10baset` drives a 10BASE-T link from PMOD B with resistors and capacitors, without an
external PHY. The wiring diagram is at the top of the script and in [Parallel PHYs](#parallel-phys).
The FPGA transmits on RJ45 pins 3 and 6 and receives on pins 1 and 2, as a switch port does: use
a straight cable to a NIC, or an auto-MDIX switch port.

The PHY runs at 100 MHz from its own output of the PLL. LED 0 shows the link and LED 1 an inverted
receive pair, which the PHY corrects. The target adds a UART bridge, so the host reaches the
registers when the link is down (`litex_server --uart`).

## 5GBASE-R on Artix-7 {#ex-a7-5g}

`ac701_5g` and `acorn_baseboard_mini_5g` connect the Artix-7 GTP 5GBASE-R PHY to a 32-bit
Etherbone core at 100 MHz, with a 256-word buffer. They are bring-up designs: a 32-bit core at
100 MHz cannot carry every 5G traffic pattern. Unlike the other targets, they have a VexRiscv CPU
and the BIOS, which program the reference clock and the SFP module. Both need validation on
hardware.

| Item | AC701 | Acorn Baseboard Mini |
|------------------------|--------------------------------------|--------------------------------------|
| Reference clock | Si5324 at 103.125 MHz, MGTREFCLK0 | Fabric PLL at 171.875 MHz, GTGREFCLK0 |
| QPLL settings | `fbdiv=5`, `fbdiv_45=5` | `fbdiv=3`, `fbdiv_45=5` |
| Clock setup | BIOS writes the Si5324 over I2C | none |
| SFP | Cage 0, I2C mux `0x74` channel 4 | Cage 0 (SFP0), `sfp_i2c` |
| Polarity | `--rx-polarity`, `--tx-polarity` | RX inverted, TX not, fixed |
| Console | Serial port | Crossover UART over JTAGBone |

The GTP line rate of 5.15625 Gbps needs a PLL at 2.578125 GHz, which 125 MHz and 156.25 MHz
references cannot give with the integer dividers. [BASE-R PHYs](#baser-phys) gives the clocking
and the other valid references. The AC701 target forwards the 156.25 MHz Si570 clock to the
Si5324, which the BIOS sets to 103.125 MHz through the I2C mux. The sequence assumes the power-on
state of the Si5324: power-cycle the board after another clock setup.

On the Acorn, the fabric reference clock needs a DRC severity override (`REQP-49`), which does
not make it jitter compliant. Check the clock quality and the link stability on the board, and
prefer a dedicated transceiver reference clock on a board that has one.

```sh
./bench/ac701_5g.py --build --output-dir build/ac701_5g --csr-csv build/ac701_5g/csr.csv
./bench/acorn_baseboard_mini_5g.py --build --output-dir build/acorn_5g \
    --csr-csv build/acorn_5g/csr.csv
```

Add `--load` to load the bitstream, and `--eth-ip` to change the address. Both scripts take the
LiteX builder and Vivado directive options; the AC701 target defaults to the `Explore`
directives and to fanout optimization, for the enable of its TX gearbox.

### SFP host mode {#ex-sfp}

The BIOS configures the SFP module for a 5GBASE-R host interface (`--sfp-host-mode 5GBASER`, the
default). This is the electrical interface between the FPGA and the module, distinct from
5GBASE-T on the cable. Support depends on the PHY and the firmware of the module, not on its
label. `--sfp-host-mode AUTO` leaves an already configured module alone.

On the BIOS console, `sfp` lists the cages and `sfp 0 5GBASER` applies the host mode again after
a module change. On the Acorn, open the console over JTAG once `litex_server --jtag` runs:

```sh
litex_term crossover --csr-csv build/acorn_5g/csr.csv
```

### PHY reset after the clock setup {#ex-a7-reset}

The reference clock of the AC701 changes after the FPGA starts, when the BIOS programs the Si5324.
Reset the PHY over JTAGBone once the BIOS has finished, then read its status. Keep TCK at 20 MHz
or less, the JTAG clock constraint of these targets.

```python
import time
from litex import RemoteClient

wb = RemoteClient(csr_csv="build/ac701_5g/csr.csv")
wb.open()
wb.regs.ethphy_reset.write(1)
time.sleep(0.01)
wb.regs.ethphy_reset.write(0)
time.sleep(0.1)
print(hex(wb.regs.ethphy_status.read()))
wb.close()
```

Look for `block_lock` and `link_up` set in `ethphy_status` ([Register reference](#reg-ethphy-baser)).
`link_up` is the receive status of the PCS, not proof of a working link in both directions: send
traffic after every reset and every cable reconnection.

## Multi-gigabit on UltraScale+ {#ex-alibaba}

`alibaba_10g` builds a 64-bit Etherbone design on the Alibaba XCKU3P board, with the PHY chosen at
build time. The reference clock is the 156.25 MHz SFP clock of the board; 25GBASE-R runs its PLL
in fractional-N mode from it. The design adds JTAGBone and the I2C masters of both SFP cages.

| Option | Default | Effect |
|------------------------|----------------|----------------------------------------------------|
| `--eth-speed` | `10g` | `1g` (1000BASE-X), `5g`, `10g` or `25g` (BASE-R) |
| `--eth-sfp` | 0 | SFP cage, 0 or 1 |
| `--eth-ip` | 192.168.1.50 | Address of the board |
| `--eth-mtu` | 1530 | Frame size of the MAC; 9022 (`eth_mtu_jumboframe`) for jumbo frames |
| `--sys-clk-freq` | 200 MHz | `sys` clock |

The `sys` clock should be faster than the PHY receive clock: 156.25 MHz at 10GBASE-R and half of
it at 5GBASE-R. At 25GBASE-R the PHY delivers 64 bits at 390.625 MHz, and the 64-bit core runs in
that clock. The Etherbone runs in `sys`: its traffic is capped at 64 bits per `sys` cycle,
12.8 Gbps at 200 MHz.

## PTP targets {#ex-ptp}

`arty_ptp` (MII) and `acorn_baseboard_mini_ptp` (1000BASE-X on SFP) add the PTP slave of
[Precision Time Protocol](#ptp) to an Etherbone design. The core joins the PTP multicast groups
224.0.1.129 and 224.0.1.130 with IGMP, plus 224.0.0.107 with `--p2p`, and the PTP core runs in a
`sys_eth` clock domain at the `sys` frequency. The host runs `ptp4l` as master; the header of each
script gives its configuration file (E2E, UDPv4, software time stamping).

```sh
sudo ptp4l -f ptp-master.cfg
./bench/test_ptp.py --count 100
```

These targets do not elaborate on this revision: they pass a `monitor_debug` argument that
`LiteEthPTP` does not take, and `test_ptp.py` reads `ptp_monitor_*` registers that the core does
not have. To use them, remove the `monitor_debug` argument (and the `--ptp-debug` option) from
the target, build it, then read `ptp_locked` and `ptp_master_ip` with
`litex_cli --regs --filter ptp`.

## 100G benches {#ex-100g}

The 100G path is in development: [BASE-R PHYs](#baser-phys) describes its status. Three scripts
exercise it without a board; none of them is a hardware test.

| Script | What it does | Needs |
|---------------------------------|------------------------------------------------------|-------------------|
| `cmac_100g.py` | Generates a CMAC IP and an out-of-context synthesis project | Vivado |
| `mac_100g.py` | Traffic through the UDP/IP core and the AXI-Stream adapter | Verilator |
| `mac_100g_timing.py` | Routed timing build of the adapter, CDC and UDP path | Vivado |

```sh
PYTHONPATH=. python3 bench/cmac_100g.py --output-dir build/cmac_100g
PYTHONPATH=. python3 bench/mac_100g.py --profile standard --packets 128
PYTHONPATH=. python3 -m bench.mac_100g_timing --output-dir build/mac_100g_timing
```

`cmac_100g.py` takes the part (`xcvu3p-ffvc1517-2-e` by default), the CMAC site and the GT group,
and writes a `build.tcl` to run with `vivado -mode batch`. `mac_100g.py` is described in
[Simulation and verification](#simulation); the recorded timing build is in
[Performance and resources](#performance).

## Host tools {#ex-tools}

| Tool | Purpose | Main options |
|----------------------------------------|----------------------------------|----------------------------------------|
| `test_etherbone.py` | Etherbone checks through `litex_server` | `--ident`, `--access`, `--sram`, `--speed`, `--udp` |
| `test_udp_streamer.py` | LEDs and switches of the `arty` target | `--leds`, `--switches`, `--ip-address` |
| `test_ptp.py` | PTP state over Etherbone | `--count`, `--interval`, `--debug`, `--plot` |
| `udp_throughput.py` | Cycles per packet of the UDP/IP path | `--data-width`, `--length`, `--packets` |

`test_etherbone.py --speed` writes and reads 16 bursts of 255 words of the SRAM (16 320 bytes,
about 16 KiB) and prints two rates in KiB/s; the rates divide 16 384 bytes by the time, 0.4 % more
than the bytes sent. `--udp` talks to 192.168.1.50 directly with `CommUDP`, without
`litex_server`; stop `litex_server` first, since both bind UDP port 1234 on the host.
The Etherbone frontend, `litex_server` and the Wireshark dissector are described in
[Etherbone, UDP streamer and raw ports](#frontends).

Where to go next: [Simulation and verification](#simulation) runs the same designs without a
board; [Debugging](#debugging) helps when a target does not answer.
