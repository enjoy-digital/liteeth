# Gowin GW5 1000BASE-X

`GW5_1000BASEX` connects LiteEth's existing 8b/10b PCS and autonegotiation
to a `GTR12_QUAD` SerDes through its raw 10-bit interface at 125 MHz.
The configuration supports **GW5AST-138B, Q1 lane 0 or 1**, with a
**100 MHz differential reference on Q1 REFCLK1**, using Gowin's toolchain.
These are the SFP-0 and SFP-1 connections on the Tang Mega 138K Pro dock.
Select one with `GW5_1000BASEX(platform, lane=0)` or `lane=1`; lane 0 remains
the default. A PHY instance uses one port at a time.

The SerDes handles comma alignment. Registers at the raw SerDes interface
and buffers at the MAC interface keep the 125 MHz datapaths short.
`ethphy_reset` resets the lane and PCS; `ethphy_status` reports transmit
PLL lock, receive CDR lock, comma alignment, and PCS link-up in bits 0–3.

Serial and reference-clock signals use dedicated package pins selected by
the SerDes configuration, as in Gowin's generated wrapper. They do not
require fabric I/O constraints. The board target must enable the SFP
transmitter and provide the reference clock. LiteX's Ethernet integration
adds the TX/RX clock constraints and asynchronous clock-domain exceptions.

## SerDes configuration

`liteeth/phy/gw5_1000basex.py` embeds the SerDes initialization register
writes and their TOML source. During the Gowin build, the project script
writes `gw5_1000basex.csr` in the gateware directory and loads it with
`set_csr`. No separate configuration files, generated/encrypted PHY IP,
or file writes during Python module construction are needed.

To modify the SerDes setup, export the embedded TOML and regenerate the
register writes with Gowin 1.9.12's generator. For lane 1:

```sh
python3 -c 'from liteeth.phy.gw5_1000basex import _serdes_toml; print(_serdes_toml.format(lane=1, other_lane=0), end="")' > gw5_1000basex.toml
/path/to/gowin/IDE/bin/serdes_toml_to_csr.dist/serdes_toml_to_csr_138k.bin \
    gw5_1000basex.toml -o gw5_1000basex.csr --comment
```

For lane 0, use `lane=0, other_lane=1`. Update the TOML template and the
corresponding `_serdes_csr[lane]` entry together after checking the generated
writes and updating the vendor-configuration checksums in the tests.
The generator requires its vendor runtime dependencies. Regeneration is
only needed when changing the SerDes configuration. The checked-in setup
configures the selected lane for 1.25 Gb/s TX/RX, 10-bit data, hardware comma
alignment, and fabric-controlled lane/PCS resets. Sharing the SerDes with
another IP requires a combined configuration.
The board target therefore rejects simultaneous 1000BASE-X and PCIe use.

## Tang Mega 138K Pro

With the corresponding LiteX-Boards target support and LiteX Gowin
false-path constraint fix installed:

```sh
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=1000basex --eth-ip=192.168.1.50 --build --load
ping 192.168.1.50
```

Add `--eth-sfp=1` to use SFP-1; `--eth-sfp=0` is the default.
Connect the selected SFP to a 1000BASE-X peer and retain the dock's 100 MHz PLL0
reference-clock setting. The target retains its default DDR3 and Etherbone
buffer configuration.

References: [Gowin Customized PHY](https://www.gowinsemi.com/en/support/ip_detail/129/),
[Sipeed SerDes examples](https://github.com/sipeed/TangMega-138KPro-example/tree/main/sfp%2B/customized_phy),
[Sipeed reference-clock setup](https://github.com/sipeed/TangMega-138KPro-example/blob/main/sfp%2B/docs/SET_5351.md).
