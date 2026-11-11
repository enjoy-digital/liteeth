# Integration {#integration}

This chapter explains how to add LiteEth to your own design. It starts with a LiteX SoC, the native
way: the software MAC for a CPU, the hardware stack with Etherbone, both together, and the UDP ports
of user logic.

It then covers the choice of data width and clocks, the timing constraints and the network side of
the BIOS. It ends with the standalone Verilog core for designs built without LiteX, and the steps to
port a PHY to a new transceiver.

## At a glance {#integ-glance}

| Item | Value |
|-------------------------|-------------------------------------------------------------------------|
| Language | Python (Migen, LiteX); Verilog through `liteeth_gen` |
| LiteX helpers | `add_ethernet`, `add_etherbone` of the LiteX SoC class |
| Software MAC | `LiteEthMAC` with SRAM slots on the CPU bus, for the BIOS and Linux |
| Hardware stack | `LiteEthUDPIPCore`: MAC, ARP, IP, ICMP, UDP, with no CPU on the path |
| User interface | UDP user ports of the crossbar; Etherbone; UDP streamer |
| Standalone core | `liteeth_gen <config>`: MAC on Wishbone or AXI-Lite, or UDP core |
| Reference designs | LiteX-Boards targets, `bench/`, `examples/` |

## Integration in a LiteX SoC {#integ-litex}

A LiteX SoC uses LiteEth in two ways, shown in the figure below. With `add_ethernet`, the CPU sends
and receives frames through the SRAM slots of the MAC, and software runs the network stack.

With `add_etherbone`, the hardware stack answers ARP and ping, and its Etherbone frontend gives the
host access to the bus of the SoC. Both can share one PHY and one MAC.

![LiteEth in a LiteX SoC. add_ethernet maps the SRAM slots of the MAC on the CPU bus;
add_etherbone adds the UDP/IP core and the Etherbone frontend, a master of the same bus. The cyan
arrow is a UDP port that user logic takes from the crossbar. With with_ethmac, one MAC serves both
paths.](integ_soc.svg)

The target creates the PHY with the pins of the board, then calls one helper. The example below
uses the MII PHY of the Arty A7; any PHY of [Supported FPGAs and boards](#boards) takes its place
with its own pins and arguments.

```python
from liteeth.phy import LiteEthPHYMII

class MySoC(SoCCore):
    def __init__(self, platform, sys_clk_freq, with_etherbone=False, **kwargs):
        SoCCore.__init__(self, platform, sys_clk_freq, **kwargs)
        # [...] Clock generator: sys, and the 25 MHz reference of the external PHY.

        # PHY: MII to the external PHY chip of the board.
        self.ethphy = LiteEthPHYMII(
            clock_pads = platform.request("eth_clocks"),
            pads       = platform.request("eth"))

        # Hardware stack with Etherbone, or software MAC for the CPU.
        if with_etherbone:
            self.add_etherbone(phy=self.ethphy, ip_address="192.168.1.50")
        else:
            self.add_ethernet(phy=self.ethphy)
```

The table below sums up what each helper builds. [Build options](#build-options) lists all their
arguments.

| Helper | Builds | Used by |
|-------------------------------|-------------------------------------------------------|---------------------------------|
| `add_ethernet` | `LiteEthMAC` with SRAM slots, region and interrupt | BIOS network commands, Linux |
| `add_etherbone` | `LiteEthUDPIPCore`, `LiteEthEtherbone` bus master | Host tools, UDP user ports |
| `add_etherbone`, `with_ethmac` | Both, on one MAC in hybrid mode | Both |

### The software MAC: add_ethernet {#integ-ethernet}

`add_ethernet` builds the MAC with its Wishbone interface and maps its SRAM slots as a region of the
bus, named `ethmac` by default, split in a receive and a transmit half.

It adds the interrupt of the MAC to the CPU when the SoC has interrupts. The BIOS and the Linux
driver find the slots and the registers through the generated headers and device tree.

The capture below, from the LiteX documentation, shows the regions of an Arty A7 SoC with the
software MAC. The `ethmac` region of 8 KiB holds two receive slots and two transmit slots of
2048 bytes each, the default; the MAC takes interrupt 2.

```text
Bus Regions: (8)
rom                 : Origin: 0x00000000, Size: 0x00020000, Mode:  RX, Cached:  True, Linker: False
[...]
ethmac              : Origin: 0x80000000, Size: 0x00002000, Mode:  RW, Cached: False, Linker:  True
ethmac_rx           : Origin: 0x80000000, Size: 0x00001000, Mode:   R, Cached: False, Linker: False
ethmac_tx           : Origin: 0x80001000, Size: 0x00001000, Mode:  RW, Cached: False, Linker: False
csr                 : Origin: 0xf0000000, Size: 0x00010000, Mode:  RW, Cached: False, Linker: False
[...]
IRQ Locations: (4)
- uart : 0, timer0 : 1, ethmac : 2, sdcard : 3
```

The arguments that matter most are in the table below. The addresses become constants of the
BIOS; [The BIOS on the network](#integ-bios) describes their use.

| Argument | Default | Effect |
|---------------------------|-----------|--------------------------------------------------------------|
| `data_width` | 8 | 8, 32 or 64: width and clock domain of the MAC datapath |
| `nrxslots`, `ntxslots` | 2, 2 | Number of receive and transmit slots |
| `local_ip`, `remote_ip` | None | Board and TFTP server addresses of the BIOS |
| `mac_address` | None | MAC address of the BIOS |
| `dynamic_ip` | False | Addresses set at run time by BIOS commands |
| `with_dhcp` | False | DHCP client in the BIOS (implies `dynamic_ip`) |
| `with_timestamp` | False | Timestamps of the frames, from the uptime of `timer0` |
| `software_debug` | False | Preamble and CRC in software, frame dumps in the BIOS |

The MAC of `add_ethernet` is 32 bits wide on the bus for a `data_width` of 8 or 32, and 64 bits
for 64. With 8, its formatting stages (padding, CRC, preamble) run in the PHY clock domains at the
width of the PHY; with 32, they run in `sys` at 32 bits. [MAC](#mac) describes the stages and the
SRAM slots.

### The hardware stack and Etherbone: add_etherbone {#integ-etherbone}

`add_etherbone` builds `LiteEthUDPIPCore` with the MAC address `10:e2:d5:00:00:00` and the IP
address 192.168.1.50 by default, and a `LiteEthEtherbone` on UDP port 1234. The Etherbone is a
master of the bus: the host reads and writes the registers and memories of the SoC through
`litex_server`, as [Getting started](#getting-started) shows.

The clock domain of the core depends on `data_width`. With 8 or 64, the core runs in the receive
clock domain of the PHY, user ports included; with 32, or in hybrid mode, it runs in `sys`.

The helper therefore adds a clock domain named `etherbone`, a copy of `sys`. A user port that lives
in the system clock asks for `cd="etherbone"`, at any data width; `cd="sys"` means the domain of the
core.

| Argument | Default | Effect |
|---------------------------|----------------------|------------------------------------------|
| `data_width` | 8 | 8, 32 or 64: width of the core; 32 runs it in `sys` |
| `mac_address` | `0x10e2d5000000` | MAC address of the hardware stack |
| `ip_address` | `"192.168.1.50"` | IP address of the hardware stack |
| `udp_port` | 1234 | UDP port of the Etherbone |
| `buffer_depth` | 16 | Data words of a record, the longest burst |
| `with_ip_broadcast` | True | No destination IP filter: every IPv4 packet is accepted; False keeps `ip_address` only |
| `with_igmp` | False | IGMP joiner for `igmp_groups`, every `igmp_interval` s |
| `eth_mtu` | 1530 | Largest frame, 9022 for jumbo frames |
| `with_ethmac` | False | Hybrid mode, see below |

Most bench targets set `buffer_depth=255` so that `test_etherbone.py --speed` sends bursts of 255
words in one record. A record longer than the buffer is dropped whole, so run `--speed` only on a
target whose buffer holds 255 words (not `xu8_st1` or `arty_10baset`). [Etherbone, UDP streamer and
raw ports](#frontends) describes the records, the buffer and the ordering of the accesses.

### Hybrid mode: with_ethmac {#integ-hybrid}

With `with_ethmac=True`, `add_etherbone` builds the core with a hybrid MAC and maps its SRAM slots
for the CPU, as `add_ethernet` would. The hardware stack keeps its own MAC and IP addresses; the
CPU takes `ethmac_address` (`0x10e2d5000001`), `ethmac_local_ip` (192.168.1.51) and
`ethmac_remote_ip` (192.168.1.100). The helper rejects a CPU address equal to the one of the
stack.

The MAC sends to the hardware stack the frames for its MAC address, to the CPU the other unicast
frames, and to both the broadcast and multicast ones. The core then runs in `sys`, and the BIOS
does not reset the PHY (`ETH_PHY_NO_RESET`), so that it cannot cut the Etherbone link. [MAC](#mac)
gives the filtering rules.

### UDP ports for user logic {#integ-udp-ports}

User logic sends and receives UDP packets through a port of the UDP crossbar of the core. A port has
a UDP port number, a data width and a clock domain; the crossbar adds the width conversion and the
clock domain crossing.

The example below, from the `arty` bench target, adds a UDP streamer on port 6000 in the system
clock.

```python
from liteeth.frontend.stream import LiteEthUDPStreamer

self.add_etherbone(phy=self.ethphy, buffer_depth=255)
self.udp_streamer = LiteEthUDPStreamer(
    udp        = self.ethcore_etherbone.udp,
    ip_address = "192.168.1.100", # Destination of the transmitted packets.
    udp_port   = 6000,
    cd         = "etherbone",     # System clock domain, see add_etherbone.
)
```

A port without a frontend comes straight from the crossbar, with the packet parameters on its
streams: `self.ethcore_etherbone.udp.crossbar.get_port(5000, dw=32, cd="etherbone")`. Each port
number can be taken once. [Etherbone, UDP streamer and raw ports](#frontends) describes the
streams of a port and the streamer.

### PTP {#integ-ptp}

`LiteEthPTP` takes two UDP ports of the crossbar, the event port 319 and the general port 320, and
runs in one clock domain. The core must join the PTP multicast groups through IGMP
(`with_igmp=True`, `igmp_groups` in `add_etherbone`). [Precision Time Protocol](#ptp) gives the
instantiation, the registers and the host setup.

## Choosing the data width and the clocks {#integ-width}

The PHY fixes the width and the clock of the Ethernet side; the core then needs a width and a clock
that carry the line rate.

The table below gives the PHY side, from the PHY classes, and the lowest core clock that carries the
line rate at the width of the core. These minimums are computed (line rate divided by width) and
leave out the header and gap overhead and the margin.

| Line rate | PHY width, PHY clock | `data_width` | Lowest core clock |
|--------------------|--------------------------|---------------------|-----------------------|
| 10 or 100 Mbps | 8 bits, 25 or 50 MHz | 8 | Any |
| 1 Gbps | 8 bits, 125 MHz (32 bits, 31.25 MHz on GateMate) | 8, or 32 | 31.25 MHz at 32 bits |
| 2.5 Gbps | 8 bits, 312.5 MHz | 32 | 78.125 MHz at 32 bits |
| 5 Gbps | 64 bits, 78.125 MHz and up | 32 or 64 | 156.25 MHz at 32 bits |
| 10 Gbps | 64 bits, 156.25 MHz and up | 64 | 156.25 MHz at 64 bits |
| 25 Gbps | 64 bits, 390.625 MHz | 128 to 512 | 195.3125 MHz at 128 |

In a LiteX SoC, the core clock is the PHY clock at 8 and 64 bits, and `sys` at 32 bits; in the
generated core, it is `sys_clock` at every width. At 8 bits, 2.5 Gbps therefore means a core at
312.5 MHz in the fabric.

The Artix-7 5GBASE-R targets run a 32-bit core at 100 MHz, below the line rate: they are bring-up
designs, and their documentation says that they cannot sustain every 5G traffic pattern.

The LiteX helpers accept 8, 32 and 64 bits. Wider cores, from 128 to 512 bits, are built with
`LiteEthUDPIPCore` directly or with the generator; the `udp_baser_25g` example runs a 256-bit core
at 200 MHz. [Core architecture](#architecture) describes the clock domains, the store-and-forward
buffers and the pipelining, and [Performance and resources](#performance) the packet rate of each
width.

## Timing constraints {#integ-timing}

Both helpers call `add_ethernet_timing_constraints` unless `with_timing_constraints=False`. It
gives the clocks of the PHY their period and declares the paths between them and `sys`
asynchronous. The table below shows the cases.

| PHY | Constraints |
|-----------------------------------|---------------------------------------------------------------|
| Parallel PHYs, most transceivers | Period of `eth_rx` and `eth_tx`; false paths to `sys` |
| UltraScale(+) BASE-X | Period of the transceiver output clocks; false paths between directions |
| Simulation model | None |

The PHY declares its clock frequencies in `rx_clk_freq` and `tx_clk_freq`; a PHY with
`txoutclk_freq` gets the second case. A design that drives its PHY clocks in another way, or that
needs other exceptions, turns the helper off and writes its own constraints.

The interface timing of the pins, such as the RGMII delays, is set in the PHY, as [MII, RMII, GMII,
RGMII, XGMII and 10BASE-T](#parallel-phys) describes.

## The BIOS on the network {#integ-bios}

With the software MAC, the LiteX BIOS has a small network stack (`libliteeth`): ARP, ICMP echo,
UDP, TFTP, DHCP, plus MDIO and SFP helpers. Its addresses come from constants that
`add_ethernet` writes from its arguments. The table below lists the constants.

| Constant | Set by | Effect |
|----------------------------------------|----------------------|------------------------------------------|
| `LOCALIP1` to `LOCALIP4` | `local_ip` | Board address, else 192.168.1.50 |
| `REMOTEIP1` to `REMOTEIP4` | `remote_ip` | TFTP server address, else 192.168.1.100 |
| `MACADDR1` to `MACADDR6` | `mac_address` | Board MAC address, else `10:e2:d5:00:00:00` |
| `ETH_DYNAMIC_IP` | `dynamic_ip` | Commands that set the addresses, and `ping` |
| `ETH_WITH_DHCP` | `with_dhcp` | DHCP before netboot, `eth_dhcp` command |
| `ETH_PHY_NO_RESET` | `with_ethmac` | No PHY reset from the BIOS |
| `ETH_NETBOOT_SKIP_JSON` | User constant | Netboot asks for `boot.bin` only |
| `NET_BOOT_DISABLE` | User constant | No netboot at power-up |

### Network boot {#integ-netboot}

At power-up, the BIOS runs its boot methods in order of priority, the network among them. The
network method gets an address over DHCP when `ETH_WITH_DHCP` is set, then runs `netboot`.
`netboot` asks the TFTP server, on UDP port 69, for `boot.json`, then for `boot.bin` at the start
of the main RAM. `netboot <file>` loads another manifest instead.

The manifest is a JSON object whose keys are file names and whose values are load addresses. The
BIOS loads every file, then jumps to the last one, or to the value of `addr`. The table below
lists the reserved keys.

| Key | Effect |
|--------------------|------------------------------------------------------------------|
| `addr` | Address to jump to, instead of the last image |
| `r1`, `r2`, `r3` | Values passed to the booted program in registers |
| `bootargs` | Object holding `addr`, `r1` to `r3`; a string value is ignored |

The BIOS checks the manifest before it loads anything: two images at one address, an image that
does not fit, or a duplicated key stop the boot with an error. [Getting started](#gs-netboot)
shows a manifest and a boot log.

### MDIO and SFP commands {#integ-bios-mdio}

The parallel PHYs with an MDIO bus expose it through the `ethphy_mdio_w` and `ethphy_mdio_r`
registers. The BIOS then offers `mdio_read <phy> <reg>`, `mdio_write <phy> <reg> <value>` and
`mdio_dump <phy> <count>` to read and write the registers of the external PHY chip, Clause 22
style, 32 registers at most.

With an I2C master and the SFP configuration, the BIOS also offers `sfp`. Without arguments, it
lists the cages and the module of each; `sfp <cage> <mode>` sets the host interface of a module to
`10GBASER`, `5GBASER`, `5000BASEX`, `2500BASEX` or `1000BASEX`.

`sfp_mdio_read` and `sfp_mdio_write` reach the Clause 45 registers of the PHY of a copper module.
The target declares a cage with `add_config`, as the Artix-7 5GBASE-R targets do:

```python
self.add_config("SFP_0_I2C",       "sfp_i2c") # Name of the I2C master of the cage.
self.add_config("SFP_0_HOST_MODE", "5GBASER") # Host interface set at boot.
```

## Standalone core: liteeth_gen {#integ-standalone}

The generator, `liteeth_gen`, writes a Verilog core from a YAML description, for designs built
without LiteX or with a core that must be versioned as a file. It elaborates a small LiteX SoC
with no CPU, writes its Verilog, constraints and register map, and stops there: it never runs the
FPGA tools.

```sh
liteeth_gen examples/wishbone_mii.yml --output-dir build --name liteeth_core
```

The outputs are `build/gateware/liteeth_core.v`, the constraints of the toolchain (an `.xdc` for
Vivado) and `build/csr.csv`, the register map. `--output-dir` and `--name` default to the values
above, and `--csr-csv` changes the path of the register map. `python3 -m liteeth.gen` runs the
same tool.

### Flavours {#integ-flavours}

The `core` key selects one of two flavours. The MAC flavour (`wishbone` or `axi-lite`) is the
software MAC of `add_ethernet`, for a CPU of your design. The `udp` flavour is the hardware stack,
with Etherbone and UDP ports for user logic.

The figure below shows the ports of a MAC core. The CPU reaches the registers and the SRAM slots
through a 32-bit Wishbone slave named `wishbone`, or an AXI-Lite slave named `bus`; the
`interrupt` output signals received and sent frames.

![The ports of a standalone MAC core. sys_clock clocks the bus side; the PHY pins depend on the
PHY of the configuration: `mii` in `wishbone_mii`. The registers and the SRAM slots share the bus.](integ_standalone_mac.svg)

The register map follows the LiteX `ethmac`, so software written for a LiteX SoC applies.

In the `wishbone_mii` example, the registers start at `0x00000000` (`ctrl`, then `ethphy` at `0x800`
and `ethmac` at `0x1000`), and the slots at `0x50000000`, set by the `soc: mem_map` key: 4 KiB of
receive slots, then 4 KiB of transmit slots. The `wishbone_adr` input is a 30-bit word address.

The figure below shows the ports of a UDP core. The MAC and IP addresses come from input pins
unless the YAML fixes them. Etherbone, when enabled, is an AXI-Lite master named `mmap`: the host
reads and writes your bus through it. Each UDP port of the YAML becomes a group of pins named
after it.

![The ports of a standalone UDP core with Etherbone and one UDP streamer port, udp0. The cyan
arrow is the stream that user logic drives and reads.](integ_standalone_udp.svg)

The module header below was generated from `examples/udp_baser.yml`: a 10GBASE-R core on an
UltraScale+ GTY, with Etherbone and one 64-bit streamer port.

```verilog
module liteeth_core (
    output wire          baser_link_up,
    input  wire          baser_refclk_n,
    (* dont_touch = "true" *)
    input  wire          baser_refclk_p,
    input  wire          baser_rst,
    [...]
    input  wire   [31:0] ip_address,
    input  wire   [47:0] mac_address,
    output wire   [31:0] mmap_araddr,
    [...]
    input  wire          sys_clock,
    input  wire          sys_reset,
    input  wire   [31:0] udp0_ip_address,
    input  wire   [63:0] udp0_sink_data,
    input  wire    [7:0] udp0_sink_keep,
    input  wire          udp0_sink_last,
    output wire          udp0_sink_ready,
    input  wire          udp0_sink_valid,
    output wire   [63:0] udp0_source_data,
    output wire          udp0_source_error,
    output wire    [7:0] udp0_source_keep,
    output wire          udp0_source_last,
    input  wire          udp0_source_ready,
    output wire          udp0_source_valid,
    input  wire   [15:0] udp0_udp_port
);
```

### UDP ports of the generated core {#integ-gen-ports}

A UDP port of the YAML has a mode. A `streamer` port, the default, is a UDP streamer: plain
`sink` and `source` streams, with `keep` byte enables unless `with_tkeep` is false. When the port
fixes `ip_address`, it also needs `udp_port`, and both are constants; without `ip_address`, the
port gets `ip_address` and `udp_port` input pins, as `udp0` above.

A `raw` port exposes the UDP user port of the crossbar: on both streams, the IP address, the
source and destination ports, the length, `be` and `error`. It receives the packets whose
destination port equals the value of its `sink_dst_port` input. The table below lists the pins of
each mode, named `<name>_<pin>`.

| Mode | Sink pins (to the network) | Source pins (from the network) |
|----------------|--------------------------------------------------|----------------------------------------------|
| `streamer` | `valid`, `last`, `ready`, `data`, `keep` | Same, plus `error` |
| `raw` | Same with `be`, plus `ip_address`, `src_port`, `dst_port`, `length` | Same, plus `error` |

### YAML keys {#integ-gen-keys}

The table below lists the main keys; [Build options](#build-options) lists every key with its
default, the tuning keys of the PHYs included.

| Key | Effect | Default |
|-----------------------------|---------------------------------------------------------|-----------------|
| `phy` | PHY class name, from `liteeth.phy` | Required |
| `vendor`, `device` | `xilinx` or `lattice`, and the FPGA part | Required, `""` |
| `toolchain` | Toolchain of the constraints | Per vendor |
| `clk_freq` | Frequency of `sys_clock`, in Hz | Required |
| `refclk_freq` | Transceiver reference clock, in Hz | Per PHY |
| `core` | `wishbone`, `axi-lite` or `udp` | Required |
| `endianness` | `big` or `little`, MAC flavour | Required |
| `nrxslots`, `ntxslots` | SRAM slots, MAC flavour | 2, 2 |
| `mac_address`, `ip_address` | Fixed addresses of the UDP core | Input pins |
| `netmask`, `gateway_ip` | Route to other subnets, both or none | None |
| `dhcp` | DHCP client of the UDP core, with `dhcp` pins | False |
| `data_width` | Width of the UDP core, 8 to 512 | 8 |
| `eth_mtu`, `jumbo_frames` | Largest frame: 1530, or 9022 with jumbo frames | 1530, False |
| `etherbone` | Etherbone with the `mmap` master, on `etherbone_port` | False, 1234 |
| `udp_ports` | UDP ports, by name, with their own keys | None |
| `soc` | Extra arguments of the SoC (`mem_map`, `csr_map`) | None |

The PHYs that the generator accepts are MII, RMII, 10BASE-T, GMII, GMII with MII fallback, RGMII on
7-Series, ECP5 and UltraScale(+), 1000BASE-X and 2500BASE-X on 7-Series GTP and GTX, UltraScale GTH
and UltraScale+ GTH and GTY, BASE-R on Kintex-7 GTX and UltraScale+ (5G, 10G and 25G), and XGMII at
64 bits.

For the BASE-R PHYs, it requires a dedicated reference clock and `phy_fec: none`.

The `examples/` directory holds ten configurations, from a Wishbone MII MAC to a 256-bit
25GBASE-R UDP core; [Examples and targets](#examples) describes them.

### Limits of the generator {#integ-gen-limits}

The generator covers fewer cases than the Python API. The list below gives the limits on the
revision of this manual.

- Vendors are `xilinx` and `lattice` only, although PHYs exist for Gowin, Efinix, Altera and
  Cologne Chip FPGAs; use the Python API for these.
- Some PHYs are not accepted: Spartan-6, Agilex, GW5, Titanium and Trion RGMII, Virtex-7 BASE-X,
  the LVDS PHYs, the GW5 and GateMate transceivers, Artix-7 5GBASE-R and the CMAC adapter.
- With `dhcp: True`, the generator does not connect the MAC address of the DHCP client, which
  sends zero as its hardware address. The DHCP result comes out on `dhcp_ip_address`; your logic
  drives it back to the `ip_address` input.
- The UDP core has no register beyond `ctrl`, and the PHYs are built without their status
  registers: `link_up` is a pin on the transceiver PHYs only.
- `examples/udp_raw_ecp5rgmii.yml` gives the streamer ports a `port` key, which the generator does
  not read: both streamers get dynamic `ip_address` and `udp_port` pins.

## Integration without LiteX {#integ-without-litex}

The generated core is one Verilog module with no parameter. Instantiate it in your design, then
connect it as below.

1. Drive `sys_clock` at the `clk_freq` of the YAML, and `sys_reset` high at start-up. The core
   generates its PHY clock domains from the PHY pins, or from the transceiver.
2. Add the generated constraints to the project. They give the PHY clocks their period and make
   their paths to `sys_clock` asynchronous; your design adds the pin locations and the I/O
   standards.
3. For a MAC core, map the bus in the address space of your CPU and take the register addresses
   from `csr.csv`. Once enabled in the event registers, the interrupt signals a received frame
   waiting in a slot and the end of a transmission.
4. For a UDP core, drive the addresses, then the user streams with the usual valid and ready
   handshake: a packet is the beats up to `last`, as [Core architecture](#architecture) defines.

A transceiver PHY also needs its reference clock on the pins that the YAML names, and the
transceiver location in the constraints of your project. The generator sets neither the location
nor the pins.

## Porting a PHY {#integ-porting}

A new transceiver or a new rate reuses the PCS of its Ethernet mode, exposes the interface of the
other PHYs to the MAC, and adds only what the device needs.

The PCS stays free of family code: the BASE-X PCS works on 10-bit symbols, the BASE-R PCS on 64-bit
blocks with a 2-bit sync header. The table below shows who owns what; the LiteEth paths are relative
to `liteeth/phy/serial/`.

| Part | Location | Owns |
|-------------------|--------------------------------|---------------------------------------------|
| BASE-X PCS | `basex/pcs.py` | 8b/10b, autonegotiation, byte stream |
| BASE-R PCS | `baser/pcs/` | 64b/66b, scrambler, block lock, BER, XGMII |
| PMAs | `basex/pma/`, `baser/pma/` | Primitive, clocks, gearbox, bitslip, reset |
| Wrappers | `basex/wrappers/`, `baser/wrappers/` | MAC stream, PCS to PMA wiring, registers |
| Transceiver helpers | `liteiclink/serdes/` | PLLs, DRP, initialization sequences |
| Board target | LiteX-Boards, `bench/` | Pins, reference clock, lane, constraints |

The steps below come from the porting notes of the repository.

1. Pick the PCS of the Ethernet mode. Define the PMA data width, the clock domains, the block
   cadence, the bitslip and the reset request before writing the primitive instance.
2. Reuse the PLL, DRP and initialization helpers of LiteICLink when their timing matches. Keep the
   reference clock and the pads in the board target.
3. Add the rate or device as a small wrapper or PMA variant. Keep the class names, the stream
   width, the register layout and the clock domain names of the existing targets, and record the
   source of each rate-dependent primitive parameter.
4. Run `python3 -m pytest -v test/test_phy_portability.py test/test_pcs_baser.py
   test/test_pcs_1000basex.py`. The first test elaborates the wrappers and checks their
   primitives and interfaces; the PCS tests check the protocol without a vendor tool.
5. Build a target with the FPGA tools, then check the constraints, the timing, the link, the
   traffic and the recovery after a reset on hardware. An elaboration checks the structure only,
   not the analog behavior or the timing.

The 7-Series PMA of BASE-R pauses its gearbox, so its PCS and XGMII adapters advance on clock
enables (`tx_ce`, `rx_ce`); UltraScale+ moves one block per clock and needs none. The wrapper
handles this difference. Channels that share a QPLL have one reset owner (`pll_master=True`) and
followers (`pll_master=False`), as [5G, 10G and 25G BASE-R, and 100G](#baser-phys) describes.

A refactor of a BASE-X PHY can be compared with the code before it: `test/phy_basex_snapshot.py`
writes structural fingerprints of the wrappers, as [PHY refactoring checks](#sim-snapshot)
describes.

## In the code {#integ-code}

`add_ethernet`, `add_ethernet_timing_constraints` and `add_etherbone` are methods of the LiteX SoC
class in `litex/soc/integration/soc.py`. The BIOS network commands are in
`litex/soc/software/bios/cmds/cmd_liteeth.c` and its netboot in `bios/boot.c`. The generator is
`liteeth/gen.py`; the porting notes are `doc/phy_portability.md` of the repository.

## Where to go next {#integ-next}

[Examples and targets](#examples) describes the YAML examples and the bench targets; [Simulation
and verification](#simulation) runs a SoC with Ethernet in Verilator; [Build options](#build-options)
lists every argument and key.
