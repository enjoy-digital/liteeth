# Porting Transceiver Ethernet PHYs

LiteEth keeps Ethernet protocol logic separate from the transceiver mode and
board wiring. A PHY port should reuse the existing PCS, expose the same MAC
interface, and add only the PMA behavior the new device needs. The broader
LiteX architecture rules are documented in
[LiteX's architectural style guide](https://github.com/enjoy-digital/litex/blob/master/doc/architectural_style.md).

## Ownership and Interfaces

| Component | Location | Responsibility |
| --- | --- | --- |
| 1000/2500BASE-X PCS | `liteeth/phy/serial/basex/pcs.py` | 8b/10b Ethernet coding, autonegotiation, and the byte stream |
| 5/10/25GBASE-R PCS | `liteeth/phy/serial/baser/pcs/` | 64b/66b coding, scrambling, block sync, BER, and XGMII |
| BASE-R PMAs | `liteeth/phy/serial/baser/pma/` | Vendor primitive in 64b/66b mode, gearbox cadence, clock/reset, and bitslip |
| PHY wrappers | `liteeth/phy/serial/{basex,baser}/wrappers/` | MAC stream, PCS/PMA wiring, public controls, and CSRs |
| Parallel PHYs | `liteeth/phy/parallel/` | MII, RMII, GMII, XGMII, and vendor RGMII adapters |
| Simulation PHY | `liteeth/phy/simulation/model.py` | Stream-based model for software and gateware simulation |
| Shared transceiver helpers | `liteiclink/serdes/` | PLLs, DRP, and applicable initialization sequences |
| Board targets and platforms | LiteX targets/platforms | Pins, reference clock source, transceiver channel, and timing constraints |
| Standalone generator | `liteeth/gen.py` | Core configuration, PHY selection, and rate-compatible reference clock defaults |

The BASE-R PCS presents a 64-bit block and two sync-header bits in each
direction. Its PMA contract includes `tx_data`, `tx_header`, `rx_data`,
`rx_header`, `rx_slip`, TX/RX clock domains, loopback, and reset. The 7-series
PMA also provides `tx_ce` and `rx_ce`: its gearbox pauses, so the PCS and
XGMII adapters must advance only when a block moves. UltraScale+ transfers
one block per user clock and does not need those enables. The PHY wrapper
handles that difference; the PCS contains no device-family conditionals.

The 1000/2500BASE-X PCS uses 8b/10b symbols and a byte stream. It cannot
share the BASE-R PCS or its 64b/66b PMA interface. Rate variants within one
transceiver family should instead share a wrapper and state their allowed
reference clocks explicitly. For example, `K7_1000BASEX` uses a 200 MHz
reference, while `K7_2500BASEX` needs 125 MHz for its 3.125 Gb/s channel
PLL configuration.

## Reuse with LiteICLink

The BASE-R GTY/GTH/GTX PMAs already reuse LiteICLink PLL, DRP, and reset/init
helpers. The 7-series GTP PMA currently reuses the older GTP initialization
logic in `liteeth/phy/serial/gtp_7series.py`. LiteICLink's generic
GTP/GTX/GTH/GTY SerDes classes contain 8b/10b encoding and are limited to
their 20/40-bit data modes. Their primitive instances cannot replace a BASE-R PMA without
adding the 64b/66b gearbox and matching its clock and reset behavior. The
device-specific primitive parameter tables therefore remain in the PMAs.

The BASE-R PHYs share their PRBS error counter and CSR layout through
`serial/baser/wrappers/diagnostics.py` (`LiteEthBASERPHY`). The PMA clocking, restart
path, XGMII pipeline, and gearbox sequencing stay in the device wrappers
because their timing differs.
Before moving another helper to LiteICLink, compare its reset sequence and
primitive parameters at every supported rate, then check on hardware. In
particular, replacing the GTP initialization path needs reset and DRP
validation on an Artix-7 board.

## Porting and Validation

1. Pick the existing PCS for the Ethernet mode. Define the PMA data width,
   clock domains, block cadence, bitslip, and reset request before writing a
   vendor primitive instance.
2. Reuse LiteICLink PLL, DRP, and initialization helpers when their timing
   contract matches. Keep the board's reference-clock and pad selection in
   its target.
3. Add the new rate or device as a small wrapper or PMA variant. Keep class
   names, stream width, CSR layout, and clock-domain names stable for existing
   targets. Record the source of rate-dependent primitive parameters.
4. Run `pytest -q test/test_phy_portability.py test/test_pcs_baser.py
   test/test_pcs_1000basex.py`. The first test elaborates supported wrappers,
   checks their primitive and public interface, and exercises the shared
   PRBS counter. The PCS tests cover protocol behavior without a vendor tool.
5. Build a representative target with the FPGA toolchain and check clock
   constraints, timing, link-up, traffic, and reset recovery on hardware.
   Elaborating a primitive verifies its structure, not analog behavior or
   timing closure.
