# Build options {#build-options}

This appendix lists the options of LiteEth in one place: the keys of the generator, the arguments
of the LiteX integration functions, the parameters of the main classes, and the options of the
bench targets and simulations. The values come from the code; `--help` prints the options of a
script with the defaults of the installed version.

## Generator {#opt-gen}

`liteeth_gen <config>.yml` reads a YAML file and writes a standalone core
([Integration](#integration)). Its command line takes the LiteX builder options, with these
defaults:

| Option | Default | Effect |
|----------------------------|------------------------------------|----------------------------------------|
| `config` | required | YAML configuration |
| `--output-dir` | `build` | Output directory |
| `--name` | `liteeth_core` | Name of the Verilog module and file |
| `--csr-csv` | `<output-dir>/csr.csv` | Register map |

### Core keys {#opt-gen-core}

| Key | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `phy` | required | PHY class name of `liteeth.phy`, see [PHY keys](#opt-gen-phy) |
| `vendor` | required | `xilinx` or `lattice`: the platform class of the outputs |
| `device` | empty | FPGA part of the platform |
| `toolchain` | `vivado`, `diamond` | Toolchain of the platform, per vendor (`trellis` on ECP5) |
| `clk_freq` | required | `sys` clock frequency, in Hz |
| `core` | required | `wishbone`, `axi-lite` (MAC) or `udp` (hardware stack) |
| `eth_mtu` | 1530 | Largest frame of the MAC, in bytes |
| `jumbo_frames` | `False` | `True` sets `eth_mtu` to 9022 when `eth_mtu` is absent |
| `tx_cdc_depth`, `rx_cdc_depth` | 32 | Depth of the clock-domain crossing FIFOs |
| `tx_cdc_buffered`, `rx_cdc_buffered` | `False` | Register stage after the crossing FIFOs, for timing |
| `soc` | none | Extra `SoCMini` arguments; `csr_map`, `interrupt_map` and `mem_map` update the maps |

### MAC flavour keys {#opt-gen-mac}

| Key | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `endianness` | required | `big` or `little`: byte order of the slots on the bus |
| `nrxslots`, `ntxslots` | 2, 2 | Number of receive and transmit slots |
| `full_memory_we` | `False` | One write enable per byte lane of the slot memories |

The MAC flavours build a 32-bit MAC with the `ethmac`, `ethmac_rx` and `ethmac_tx` regions and an
`interrupt` output; `soc: {mem_map: {ethmac: <address>}}` places the region.

### UDP flavour keys {#opt-gen-udp}

| Key | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `mac_address` | input pins | MAC address of the core; absent: a `mac_address` input |
| `ip_address` | input pins | IP address; absent: an `ip_address` input; not with `dhcp` |
| `netmask`, `gateway_ip` | none | Subnet and gateway; both or none |
| `dhcp` | `False` | DHCP client with a `dhcp` group of signals |
| `data_width` | 8 | Width of the core; 32 puts the datapath in `sys` |
| `icmp_fifo_depth` | 128 | ICMP echo buffer, in bytes |
| `etherbone` | `False` | Etherbone master on an AXI-Lite `mmap` interface |
| `etherbone_port` | 1234 | UDP port of the Etherbone |
| `etherbone_buffer_depth` | 16 | Words of the Etherbone record buffer |
| `udp_ports` | none | User ports, by name, see below |

Each entry of `udp_ports` gives one group of ports, named after it, with these keys:

| Key | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `mode` | `streamer` | `streamer` (FIFO, packetization) or `raw` (full UDP port) |
| `data_width` | 8 | Width of the port, in bits |
| `udp_port`, `ip_address` | input pins | UDP port and remote address; fixed only when `ip_address` is given |
| `tx_fifo_depth`, `rx_fifo_depth` | 64, 64 | Streamer FIFO depths, in words |
| `tx_max_packet_length` | none | Streamer: split packets not closed by `last` at this length, in bytes |
| `with_tkeep` | `True` | Streamer: `sink_keep` and `source_keep` byte enables |

A raw port reads `data_width` only: the user logic gives the ports, address and length of each
packet on its pins.

### PHY keys {#opt-gen-phy}

The generator accepts the PHYs below; the others need a LiteX SoC
([Supported FPGAs and boards](#boards)).

| `phy` values | Pins | Keys |
|--------------------------------------------------|--------------|----------------------------------------|
| `LiteEthPHYMII`, `LiteEthPHYRMII` | `mii`, `rmii` | none |
| `LiteEthPHY10BASET` | `baset` | none (runs from `sys`) |
| `LiteEthPHYGMII`, `LiteEthPHYGMIIMII` | `gmii` | none |
| `LiteEthS7PHYRGMII`, `LiteEthECP5PHYRGMII`, `LiteEthUSPHYRGMII` | `rgmii` | RGMII keys |
| `A7_1000BASEX`, `A7_2500BASEX` | `sgmii` | BASE-X and QPLL keys |
| `K7_*`, `KU_*`, `USP_GTH_*`, `USP_GTY_*` (1000/2500BASE-X) | `sgmii` | BASE-X keys |
| `K7_GTX_*`, `USP_GTH_*`, `USP_GTY_*` (5/10/25G BASE-R) | `baser` | BASE-R keys |
| `LiteEthPHYXGMII` | `xgmii` | none (64-bit) |

| Key | Default | PHYs | Effect |
|------------------------------------|----------------|--------------------|--------------------------------|
| `phy_tx_delay`, `phy_rx_delay` | 2e-9 | RGMII | Clock to data delays added by the FPGA |
| `phy_hw_init_reset` | `False` | RGMII | Reset pulse to the external PHY at start |
| `phy_dynamic_link` | `False` | ECP5 RGMII | 10/100/1000 rate adaptation (experimental) |
| `iodelay_clk_freq` | 300e6 | US RGMII | IDELAYCTRL reference clock |
| `usp` | `False` | US RGMII | UltraScale+ primitives |
| `refclk_freq` | per PHY | BASE-X, BASE-R | Reference clock; 125e6 or 156.25e6 on Artix-7 BASE-X; default 156.25e6 on BASE-R (161.1328125e6 in `udp_baser_25g`) |
| `phy_tx_polarity`, `phy_rx_polarity` | 0 | BASE-X, BASE-R | Invert the differential pairs |
| `qpll` | `True` | Artix-7 BASE-X | Instantiate the QPLL; `False`: `sgmii_qpll` pins |
| `qpll_channel` | 0 | Artix-7 BASE-X | QPLL channel, 0 or 1 |
| `phy_tx_cm_type`, `phy_rx_cm_type` | `MMCM` | Artix-7 BASE-X | Clocking of the transceiver user clocks |
| `phy_tx_cm_buf_type`, `phy_rx_cm_buf_type` | `BUFH`, `BUFG` | Artix-7 BASE-X | Clock buffers |
| `refclk_from_fabric` | `True` | US+ BASE-X | Reference from the fabric; BASE-R requires `False` |
| `phy_fec` | `none` | BASE-R | Only `none` is accepted |

The transceiver PHYs of the generator have no registers (`with_csr=False`): the core exposes
`rst` and `link_up` pins instead.

## LiteX integration {#opt-litex}

The LiteX SoC builds LiteEth with two functions, described in [Integration](#integration).

| `add_ethernet` argument | Default | Effect |
|-----------------------------------------|-------------------------|--------------------------------------|
| `name` | `ethmac` | Name of the MAC, its region and its interrupt |
| `phy`, `phy_cd` | required, none | PHY, and the prefix of its clock domains when renamed |
| `data_width` | 8 | 8, 32 or 64; 32 puts the MAC datapath in `sys` |
| `nrxslots`, `ntxslots` | 2, 2 | Receive and transmit slots |
| `rxslots_read_only`, `txslots_write_only` | `True`, `False` | Access mode of the slot regions |
| `full_memory_we` | `False` | One write enable per byte lane |
| `with_timestamp` | `False` | Time stamps of the slots, from the `timer0` uptime |
| `dynamic_ip`, `with_dhcp` | `False` | BIOS address set at run time, or by DHCP |
| `local_ip`, `remote_ip`, `mac_address` | none | BIOS constants of the addresses |
| `software_debug` | `False` | No preamble and CRC in the MAC; BIOS UDP debug output |
| `with_timing_constraints` | `True` | Period and false-path constraints of the PHY clocks |

| `add_etherbone` argument | Default | Effect |
|-----------------------------------------|-------------------------|--------------------------------------|
| `name` | `etherbone` | Name of the Etherbone master and its clock domain |
| `phy`, `phy_cd` | required, none | PHY, and the prefix of its clock domains when renamed |
| `data_width` | 8 | 8, 32 or 64; 32 puts the core in `sys` |
| `mac_address` | `0x10e2d5000000` | MAC address of the core |
| `ip_address` | 192.168.1.50 | IP address of the core |
| `udp_port` | 1234 | UDP port of the Etherbone |
| `buffer_depth` | 16 | Words of the Etherbone record buffer |
| `arp_entries` | 1 | Entries of the ARP cache |
| `with_ip_broadcast` | `True` | No destination IP filter (any address, broadcast, multicast); `False`: own address only |
| `eth_mtu` | 1530 | Largest frame of the MAC |
| `with_igmp`, `igmp_groups`, `igmp_interval` | `False`, none, 10 | IGMP membership reports, groups, period in seconds |
| `with_ethmac` | `False` | Hybrid mode: the software MAC on the same PHY |
| `ethmac_address` | `0x10e2d5000001` | MAC address of the software MAC |
| `ethmac_local_ip`, `ethmac_remote_ip` | .51, .100 | BIOS addresses of the software MAC, in 192.168.1.0/24 |
| `with_timing_constraints` | `True` | Period and false-path constraints of the PHY clocks |

## Core parameters {#opt-core}

The main classes take the parameters below; the PHY classes are described with their families in
the chapters of Part III.

| `LiteEthMAC` parameter | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `phy`, `dw` | required | PHY and width of the MAC interface, a multiple of 8 |
| `interface` | `crossbar` | `crossbar` (hardware stack), `wishbone` or `hybrid` |
| `endianness` | `big` | Byte order of the slots on the bus |
| `with_preamble_crc` | `True` | Preamble and CRC in the MAC |
| `nrxslots`, `ntxslots` | 2, 2 | SRAM slots |
| `hw_mac` | none | MAC address of the hardware stack (hybrid filter) |
| `timestamp` | none | Time stamp signal latched with each slot |
| `with_sys_datapath` | `False` | Datapath in `sys` instead of the PHY clocks |
| `tx_cdc_depth`, `rx_cdc_depth` | 32 | Clock-domain crossing FIFOs |
| `tx_cdc_buffered`, `rx_cdc_buffered` | `False` | Register stage after the crossing FIFOs |
| `eth_mtu` | 1530 | Largest frame, in bytes |
| `with_store_and_forward` | `auto` | Whole-frame buffering; `auto` decides from the rates |

`LiteEthIPCore` and `LiteEthUDPIPCore` take `phy`, `mac_address`, `ip_address` and `clk_freq`,
then the parameters below; they pass the MAC ones through. Their clock-domain crossing FIFOs are
buffered by default (`True`), unlike those of the MAC alone.

| Core parameter | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `dw` | 8 | Width of the core |
| `arp_entries` | 1 | ARP cache entries |
| `with_icmp`, `icmp_fifo_depth` | `True`, 128 | ICMP echo, buffer in bytes |
| `with_igmp`, `igmp_groups`, `igmp_interval` | `False`, none, 10 | IGMP membership reports |
| `with_ip_broadcast` | `True` | No destination IP filter (any address, broadcast, multicast); `False`: own address only |
| `gateway_ip`, `netmask` | none | Gateway and subnet, both or none |
| `interface`, `endianness` | `crossbar`, `big` | MAC interface, as above |

| Frontend | Parameters and defaults |
|-----------------------------------|---------------------------------------------------------------------|
| `LiteEthEtherbone` | `udp`, `udp_port`, `mode="master"` or `"slave"`, `buffer_depth=4`, `cd="sys"` |
| `LiteEthUDPStreamer` | `udp`, `ip_address`, `udp_port`, `data_width=8`, `rx_fifo_depth=64`, `tx_fifo_depth=64`, `with_broadcast=True`, `cd="sys"`, `with_be=False`, `tx_max_packet_length=None` |
| `udp.crossbar.get_port` | `udp_port`, `dw=8`, `cd="sys"`, `depth=None`, `tx_buffer_depth=None` |
| `LiteEthDHCP` | `udp_port` (a 32-bit user port), `sys_clk_freq`, `timeout=1` second |
| `LiteEthPTP` | `event_port`, `general_port`, `sys_clk_freq`, `timeout=1.0`, `announce_timeout=None`, `require_announce=False`, `unicast_delay_req=False` |

## Bench targets {#opt-targets}

Every board target takes `--build` (run the FPGA toolchain) and `--load` (load the bitstream,
volatile). Without them, a target elaborates the design, except `alibaba_10g`. The 100G scripts
`cmac_100g` and `mac_100g_timing` only write their outputs; they have no `--build` or `--load`. The targets below have more
options; [Examples and targets](#examples) describes them.

| Target | Options (default) |
|------------------------------------------|--------------------------------------------------------------|
| `arty_10baset` | `--ip-address` (192.168.1.50) |
| `arty_ptp` | `--p2p`, `--ptp-debug` |
| `acorn_baseboard_mini_ptp` | `--variant` (`cle-215+`), `--programmer` (`openfpgaloader`), `--sys-clk-freq` (125 MHz), `--sfp` (0), `--p2p`, `--ptp-debug` |
| `alibaba_10g` | `--eth-speed` (`10g`), `--eth-sfp` (0), `--eth-ip`, `--eth-mtu` (1530), `--sys-clk-freq` (200 MHz) |
| `ac701_5g` | `--sys-clk-freq` (100 MHz), `--eth-ip`, `--sfp-host-mode` (`5GBASER`), `--rx-polarity`, `--tx-polarity` (0), builder and Vivado options |
| `acorn_baseboard_mini_5g` | `--variant` (`cle-215+`), `--sys-clk-freq` (100 MHz), `--eth-ip`, `--sfp-host-mode` (`5GBASER`), builder and Vivado options |
| `cmac_100g` | `--output-dir`, `--part` (`xcvu3p-ffvc1517-2-e`), `--site` (`CMACE4_X0Y0`), `--gt-group` (`X0Y0~X0Y3`) |
| `mac_100g_timing` | `--output-dir`, `--part`, `--sys-clk-freq` (250 MHz) |

`--ptp-debug` is passed to `LiteEthPTP` as `monitor_debug`, a parameter that `LiteEthPTP` does not
take, so these two targets do not elaborate: see [PTP targets](#ex-ptp).

## Simulations {#opt-sim}

| `mac_100g.py` option | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `--profile` | `mixed` | `minimum`, `standard`, `jumbo` or `mixed` payload lengths |
| `--packets` | 128 | Number of packets |
| `--rate` | 100e9 | Offered receive rate, in bits per second |
| `--data-width` | 512 | Width of the core: 128, 256 or 512 |
| `--sys-clk-freq` | 250 MHz | `sys` clock |
| `--rx-clk-freq`, `--tx-clk-freq` | 322.265625 MHz | MAC clocks |
| `--fifo-depth` | 256 | Queue depth |
| `--stall-cycles`, `--error-every`, `--reset` | 0, 0, off | Fault injection |
| `--generate-only`, `--output-dir` | off, `build/mac_100g` | Write the simulation without running it |

| `udp_throughput.py` option | Default | Effect |
|---------------------------------------|-------------------------|----------------------------------------|
| `--data-width` | 64 128 256 512 | Widths to measure |
| `--length` | 18 64 65 1472 8972 | UDP payload lengths, in bytes |
| `--packets` | 32 | Packets per measurement |
| `--output-dir` | none | Write the Verilog of each width instead of simulating |

`litex_sim` takes `--with-ethernet`, `--with-etherbone`, `--ethernet-phy-model` (`sim`, `gmii`
or `xgmii`), `--local-ip` (192.168.1.50) and `--remote-ip` (192.168.1.100); `bench/dhcp_tap.py`
is described in [Simulation and verification](#simulation).
