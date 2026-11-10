# Vocabulary of the LiteEth manual

Project-specific writing rules, completing the generic `STYLE.md` of the manual kit.

## Names of the parts

- *the PHY* is a LiteEth PHY class (`LiteEthPHYMII`, `LiteEthPHYRGMII`, `A7_1000BASEX`,
  `USP_GTY_10G_BASER` and the other PHY classes): the gateware between the MAC and the pins or the
  transceiver; *the external PHY* is a PHY chip on the board; *the transceiver* is the FPGA serial
  transceiver (GTP, GTX, GTH, GTY, GW5 SerDes) used by the BASE-X and BASE-R PHYs; *the PCS* and
  *the PMA* are the sublayers of these PHYs.
- *the MAC* is `LiteEthMAC` (preamble, CRC, padding, gap, crossbar); *the core* is the MAC plus the
  hardware ARP, IP, ICMP and UDP layers (`LiteEthIPCore`, `LiteEthUDPIPCore`); *a layer* is one of
  them (*the ARP layer*, *the IP layer*, *the UDP layer*).
- *a frontend* is a block on top of the core: the Etherbone, the UDP streamer, a raw UDP port, the
  AXI-Stream adapter; *a user port* is a port returned by a crossbar `get_port`.
- *software MAC*: the MAC with its SRAM slots (`interface="wishbone"`), driven by a CPU running the
  BIOS or Linux; *hardware stack*: the core, with no CPU on the packet path; *hybrid mode*: both on
  one PHY (`interface="hybrid"`, `with_ethmac` in LiteX), the hardware stack taking its own traffic
  and the CPU the rest.
- *the link partner* is the device at the other end of the cable (switch, NIC, other board); *the
  host* is the PC that talks to the FPGA (`ping`, `litex_server`, test scripts).
- *the generator* is `liteeth_gen`; *target* is a script of `bench/`; *example* is a YAML file of
  `examples/`; *the BIOS* is the LiteX BIOS.

## Protocol names

- *frame* is an Ethernet frame (destination MAC address to FCS; preamble and SFD added on the wire);
  *packet* is an ARP, IP, ICMP, UDP or Etherbone unit carried in a frame; *beat* is one word of a
  stream (one cycle with `valid` and `ready` high); *record* is an Etherbone record.
- Interface names in capitals: MII, RMII, GMII, RGMII, SGMII, XGMII, MDIO, MDC. Line modes as IEEE
  802.3 writes them (10BASE-T, 100BASE-TX, 1000BASE-T, 1000BASE-X, 5GBASE-R, 10GBASE-R, 25GBASE-R,
  100GBASE-R), and 2500BASE-X, the common industry name of 2.5GBASE-X. Codings as 8b/10b and
  64b/66b.
- Ordered sets and code groups as 802.3 writes them: /C/, /I/, /S/, /T/, /R/, K28.5.
- Protocols in capitals: ARP, IPv4, ICMP, UDP, DHCP, IGMP, TFTP, PTP; Etherbone in title case. Cite
  IEEE 802.3 by Clause with a capital (Clause 22, Clause 36), a numbered item as a subclause
  (subclause 46.3.1.4), and RFCs by number (RFC 791), only when sure.
- Line rates in Mbps and Gbps (1.25 Gbps, 10.3125 Gbps); clock frequencies in MHz. Throughputs in
  the unit the tool or public source prints (MiB/s, KiB/s): quote them as printed and say so. This
  rule overrides the `MB/s` rule of `STYLE.md`. Sizes in bytes ("a 64-byte frame").
- *register* for a CSR exposed to software, named as software sees it, with its prefix
  (`ethphy_mdio_w`, `ethmac_sram_writer_slot`).
- Clock domains in code font: `eth_tx`, `eth_rx`, `sys`; data widths in bits (`dw=8`, a 64-bit
  datapath).

## Placeholders

- `<target>` is a target script name of `bench/` without `.py`; `<config>` is a generator YAML file
  of `examples/`. Both are defined once in the Conventions of the front matter. Addresses use the
  LiteX defaults: 192.168.1.50 for the FPGA, 192.168.1.100 for the host.

## Hardware names

- FPGA families: Spartan-6, Artix-7, Kintex-7, Virtex-7, Kintex UltraScale, Kintex/Virtex
  UltraScale+, Lattice ECP5, Efinix Trion/Titanium, Gowin GW5A, Altera Agilex, Cologne Chip
  GateMate.
- Transceivers as the vendor names them: GTP, GTX, GTH, GTY; CMAC for the UltraScale+ 100G hard MAC.
- Boards as LiteX-Boards names them (Arty A7, KC705, KCU105, Genesys2, ButterStick, Colorlight
  5A-75B, XCU1525, Tang Mega 138K Pro). Modules as SFP, SFP+, SFP28, QSFP28; DAC for a direct
  attach copper cable.
