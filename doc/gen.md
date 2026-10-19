# Standalone core generation

LiteEth is normally used as a Python package inside a LiteX SoC. For a traditional flow
(no Migen/LiteX at build time, versioned/packaged core, ...), a standalone Verilog core can be
generated from a YAML description with `liteeth_gen` (`python3 -m liteeth.gen`):
```sh
$ liteeth_gen examples/wishbone_mii.yml [--output-dir build] [--name liteeth_core]
```
This produces `build/gateware/liteeth_core.v` (plus the toolchain constraints) and `build/csr.csv`
(register map). `--output-dir`, `--name` and `--csr-csv` are optional (defaults shown above).

## Flavours

Two flavours are available through the `core` key:
- `wishbone` / `axi-lite`: MAC with SRAM slots exposed on a Wishbone or AXI-Lite slave and an
  `interrupt` output, driven by software (same register layout as the LiteX `ethmac`).
- `udp`: hardware UDP/IP stack (ARP / ICMP / UDP, optional DHCP and Etherbone) with UDP ports
  exposed as streamer or raw ports; the `mac_address` / `ip_address` come from pads when not
  fixed in the YAML.

## YAML keys

Main keys (see `liteeth/gen.py` for the complete list and the PHY tuning keys):

| Key                                                  | Description                                                   | Default           |
|------------------------------------------------------|---------------------------------------------------------------|-------------------|
| `phy`                                                | PHY class name from `liteeth.phy` (`LiteEthPHYMII`, `LiteEthS7PHYRGMII`, `A7_1000BASEX`, `USP_GTY_10G_BASER`, `LiteEthPHYXGMII`, ...) | required |
| `vendor`, `device`, `toolchain`                      | `xilinx` (`vivado`) or `lattice` (`diamond`) platform         | required / `""` / per vendor |
| `clk_freq`, `refclk_freq`                            | System clock / transceiver reference clock (Hz)               | required / per PHY |
| `core`                                               | `wishbone`, `axi-lite` or `udp`                               | required          |
| `endianness`                                         | `big` or `little` (MAC flavours)                              | required          |
| `nrxslots`, `ntxslots`                               | MAC SRAM slots (MAC flavours)                                 | 2 / 2             |
| `mac_address`, `ip_address`, `netmask`, `gateway_ip` | Addresses of the `udp` core (pads when omitted)               | pads              |
| `dhcp`                                               | DHCP client of the `udp` core (`dhcp` pads)                   | False             |
| `data_width`                                         | `udp` core datapath width (8 to 512)                          | 8                 |
| `eth_mtu`, `jumbo_frames`                            | MTU (1530, 9022 with jumbo frames)                            | 1530 / False      |
| `etherbone`, `etherbone_port`, `etherbone_buffer_depth` | Etherbone with an AXI-Lite `mmap` master                   | False / 1234 / 16 |
| `udp_ports`                                          | `{name: {udp_port, ip_address, data_width, mode: streamer or raw, tx_fifo_depth, rx_fifo_depth}}` | none |
| `tx_cdc_depth`, `rx_cdc_depth`, `*_cdc_buffered`     | PHY/sys clock domain crossing FIFOs                           | 32 / 32 / False   |
| `soc`                                                | Extra `SoCMini` arguments (`csr_map`, `mem_map`, ...)         | none              |

## Examples

Configurations in `examples/`:
- `wishbone_mii.yml` / `axi-lite-mii.yml`: MII MAC on a Wishbone / AXI-Lite bus.
- `udp_s7phyrgmii.yml`: 7-Series RGMII UDP core with streamer ports.
- `udp_raw_ecp5rgmii.yml`: ECP5 RGMII UDP core with raw and streamer ports (Lattice Diamond).
- `udp_a7_gtp_sgmii.yml` / `udp_usp_gth_sgmii.yml`: 1000BASE-X/SGMII on Artix-7 GTP / UltraScale+ GTH with DHCP.
- `udp_xgmii_jumbo.yml`: XGMII 10G UDP core with jumbo frames and Etherbone.
- `udp_baser.yml` / `udp_baser_25g.yml`: native 10GBASE-R / 25GBASE-R on UltraScale+ GTY.
- `udp_10baset.yml`: 10BASE-T on FPGA IOs.
