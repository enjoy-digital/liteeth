# Gowin GW5 1000BASE-X

`GW5_1000BASEX` connects LiteEth's existing 8b/10b PCS and autonegotiation
to a `GTR12_QUAD` SerDes through its raw 10-bit interface at 125 MHz.
The initial configuration supports **GW5AST-138B, Q1 lane 0**, with a
**100 MHz differential reference on Q1 REFCLK1**, using Gowin's toolchain.
This is the SFP-0 connection on the Tang Mega 138K Pro dock.

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

`liteeth/phy/gw5_1000basex.csr` contains the SerDes initialization register
writes. The PHY adds it to the Gowin project with `set_csr`; no separately
generated or encrypted PHY IP is needed for a normal build.

The accompanying TOML records the configuration used to produce these
writes with Gowin 1.9.12's generator:

```sh
/path/to/gowin/IDE/bin/serdes_toml_to_csr.dist/serdes_toml_to_csr_138k.bin \
    liteeth/phy/gw5_1000basex.toml \
    -o liteeth/phy/gw5_1000basex.csr --comment
```

The generator requires its vendor runtime dependencies. Regeneration is
only needed when changing the SerDes configuration. The checked-in setup
enables only Q1 lane 0: 1.25 Gb/s TX/RX, 10-bit data, hardware comma
alignment, and fabric-controlled lane/PCS resets. Other lanes are disabled;
sharing the SerDes with another IP requires a combined configuration.
The board target therefore rejects simultaneous 1000BASE-X and PCIe use.

## Tang Mega 138K Pro

With the corresponding LiteX-Boards target support and LiteX Gowin
false-path constraint fix installed:

```sh
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=1000basex --eth-ip=192.168.1.50 --build --load
ping 192.168.1.50
```

Connect SFP-0 to a 1000BASE-X peer and retain the dock's 100 MHz PLL0
reference-clock setting. The target retains its default DDR3 and Etherbone
buffer configuration.

References: [Gowin Customized PHY](https://www.gowinsemi.com/en/support/ip_detail/129/),
[Sipeed SerDes examples](https://github.com/sipeed/TangMega-138KPro-example/tree/main/sfp%2B/customized_phy),
[Sipeed reference-clock setup](https://github.com/sipeed/TangMega-138KPro-example/blob/main/sfp%2B/docs/SET_5351.md).
