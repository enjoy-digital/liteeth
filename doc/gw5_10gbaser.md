# Gowin GW5 10GBASE-R

`GW5_10G_BASER` connects LiteEth's BASE-R PCS (64b/66b coding, scrambling,
block synchronization and BER monitoring) to a `GTR12_QUAD` SerDes through
its raw 64-bit interface at 10.3125 Gb/s. The configuration supports
**GW5AST-138B, Q1 lane 0 or 1**, with a **125 MHz differential reference on
Q1 REFCLK0**, using Gowin's toolchain. These are the SFP-0 and SFP-1
connections on the Tang Mega 138K Pro dock, whose second MS5351 (PLL1)
provides Q1 REFCLK0 at 125 MHz by default. Select one SFP with
`GW5_10G_BASER(platform, lane=0)` or `lane=1`. A PHY instance uses one
port at a time.

## Datapath

The SerDes uses a 16-bit internal width and a 1:4 fabric ratio. The fabric
word is 64 bits at linerate/64 (161.13 MHz), carried in the low 8 bits of
eight 10-bit slots, slot 0 first; bit 0 of each slot is transmitted first.

GW5AST-138 lanes do not provide a hardware 64b/66b gearbox (Gowin's own
10G Serial Ethernet IP also implements it in fabric), so the PMA implements
both gearboxes:

- TX: 66-bit blocks are shifted into a two-block window; the PCS and XGMII
  adapter advance on 32 of every 33 cycles (`tx_ce`).
- RX: the 64-bit words are shifted into a three-word window and 66-bit blocks
  are extracted on 32 of every 33 cycles (`rx_ce`). A PCS bitslip request
  drops one received bit, so block synchronization reaches any boundary.

Only the small window offsets have feedback; words/blocks are extracted
through two registered mux stages. The PCS uses the transceiver-gearbox bit
order of the Xilinx BASE-R PHYs; the gearboxes convert it to transmission
order.

To close timing at 161.13 MHz on the GW5 fabric, the XGMII words are
registered between the XGMII adapters and the PCS, and the receive path uses
the PCS `with_pipelining=2` level (per-lane block classification and
pre-decoding stages) and the XGMII RX adapter pipelining. These only add
latency.

## Status / Control

The standard BASE-R diagnostics are available (`ethphy_status`,
`ethphy_control`). `ethphy_serdes_status` reports the transmit PLL lock and
the receive CDR lock. `ethphy_reset` resets the lane and the PCS. The PRBS31
generator/checker is disabled by default to save timing and can be enabled
with `with_prbs=True` for bring-up. The loopback control is not supported:
the GW5 loopback mode is part of the SerDes configuration.

Serial and reference-clock signals use dedicated package pins selected by
the SerDes configuration and do not require fabric I/O constraints. The
board target must enable the SFP transmitter. LiteX's Ethernet integration
adds the TX/RX clock constraints and asynchronous clock-domain exceptions.

## SerDes configuration

`liteeth/phy/serial/baser/pma/gw5_config.py` embeds the SerDes initialization
register writes and their TOML source. During the Gowin build, the project
script writes `gw5_10gbaser.csr` in the gateware directory and loads it with
`set_csr`. Gowin 1.9.12.04 or later is required: older toolchains reject some
of the register writes of the 1.9.12.04 generator.

10.3125 Gb/s cannot be generated from the 100 MHz reference used by
`GW5_1000BASEX` (the CPLL feedback divider would exceed its range); 125 MHz
and 156.25 MHz references are supported by the SerDes.

The generated writes are used unchanged except for the CDR VCO bias: the
1.9.12.04 generator programs a 10.3125 Gb/s CDR VCO bias (`vcobias=8`) that
does not lock to 10GBASE-R link partners on the Tang Mega 138K Pro, while
Gowin's 10G Serial Ethernet IP uses `vcobias=14`. `_cdr_vco_bias` overrides
the single affected register write (`q1.lnX.CDR.config_cdr`).

To modify the SerDes setup, export the embedded TOML and regenerate the
register writes with Gowin 1.9.12.04's generator. For lane 1:

```sh
python3 -c 'from liteeth.phy.serial.baser.pma.gw5_config import _serdes_toml; print(_serdes_toml.format(lane=1, other_lane=0), end="")' > gw5_10gbaser.toml
/path/to/gowin/IDE/bin/serdes_toml_to_csr.dist/serdes_toml_to_csr_138k.bin \
    $PWD/gw5_10gbaser.toml -o $PWD/gw5_10gbaser.csr --comment
```

For lane 0, use `lane=0, other_lane=1`. The generator requires absolute
paths. Update the TOML template and the corresponding
`_serdes_csr_generated[lane]` entry together, check that the CDR VCO bias
override still applies, and update the vendor-configuration checksums in the
tests. Sharing the SerDes with another IP requires a combined configuration;
the board target therefore rejects simultaneous 10GBASE-R and PCIe use.

## Tang Mega 138K Pro

```sh
python3 -m litex_boards.targets.sipeed_tang_mega_138k_pro \
    --with-etherbone --eth-phy=10gbaser --eth-ip=192.168.1.50 --build --load
ping 192.168.1.50
```

Add `--eth-sfp=1` to use SFP-1. Connect the selected SFP+ to a 10GBASE-R
link partner and retain the dock's 125 MHz PLL1 reference-clock setting.

References: [Gowin Customized PHY](https://www.gowinsemi.com/en/support/ip_detail/129/),
[Sipeed SerDes examples](https://github.com/sipeed/TangMega-138KPro-example/tree/main/sfp%2B),
[Sipeed reference-clock setup](https://github.com/sipeed/TangMega-138KPro-example/blob/main/sfp%2B/docs/SET_5351.md).
