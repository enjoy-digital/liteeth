# PHY Layout and Compatibility

The PHY tree has two jobs: expose PHY classes to targets and the core
generator, and implement the electrical interface or Ethernet line code.
Established public imports remain available when implementation files move.

| PHY group | Current implementation | Public entry points |
| --- | --- | --- |
| MII, RMII, GMII, XGMII | `liteeth/phy/parallel/` | Original module names and the classes in `liteeth.phy` |
| Vendor RGMII | `liteeth/phy/parallel/rgmii/` | Original vendor module names and the classes in `liteeth.phy` |
| Simulation model | `liteeth/phy/simulation/model.py` | `liteeth.phy.model` |
| 1000/2500BASE-X | `serial/basex/` with `pcs.py`, `pma/`, and `wrappers/` | Original module names and the classes in `liteeth.phy` |
| 5/10/25GBASE-R | `serial/baser/` with `pcs/`, `pma/`, and `wrappers/` | Canonical `liteeth.phy.serial.baser.*` imports |
| Shared 7-series GTP initialization | `serial/gtp_7series.py` | `liteeth.phy.a7_gtp` |
| MDIO and hardware-reset helpers | `liteeth/phy/common.py` | `liteeth.phy.common` |

The standalone generator selects a PHY by looking up its YAML `phy` value in
`liteeth.phy`. These names, the `LiteEthPHY` autodetection function, and the
established direct module imports must remain available during a reorganization.
BASE-R was recently introduced, so its former top-level `baser`, `pcs_baser`,
`pma_baser`, and device wrapper paths are not retained.

Serial Ethernet modes are grouped under `liteeth/phy/serial/`. The 64b/66b
BASE-R PCS, PMAs, diagnostics, and wrappers live in `baser/`; the 8b/10b
BASE-X PCS and device adapters live in `basex/`. Both modes put device PHYs
in `wrappers/`, with device primitives and raw serialization helpers in `pma/`.
BASE-X keeps its Ethernet PCS in one module; BASE-R uses a multi-module PCS
for its 64b/66b interface. PMA filenames describe the transceiver family;
wrapper filenames describe the FPGA-facing PHY. GW5's generated register/TOML
data lives beside its adapter in `pma/gw5_config.py`.
The established BASE-X module names are
registered as aliases in `liteeth.phy.__init__`, so code that patches a
module-level PCS helper still affects the class implementation.
Moved LVDS/SerDes helpers remain importable from their established wrappers,
and `PCSGearbox` remains importable from the established PCS module. These
are re-exports from substantive implementation modules, not forwarding files.
MII, RMII, GMII, GMII/MII, and XGMII implementations sit directly under
`liteeth/phy/parallel/`; vendor RGMII adapters are under `parallel/rgmii/`.
The stream-based PHY model lives under `simulation/`. Their established module
names are registered in the same alias table. The 7-series GTP initialization
helper is shared by BASE-X and BASE-R, so it sits above both under `serial/`.
Only the shared MDIO and hardware-reset helpers remain in the top-level
`common.py`. Keep device-specific primitive parameters visible; sharing a PLL
or reset helper does not imply that two line codes share a PMA.

## Adding or Changing a PHY

- Put Ethernet coding, autonegotiation, and packet behavior in the relevant PCS.
- Put device primitives, serialization, clock recovery, and device initialization
  in a mode-specific PMA. Keep primitive parameters and clock/reset sequencing visible.
- Use a device wrapper to connect the PCS and PMA, expose the MAC stream, and
  provide public controls and CSRs. Keep rate variants small and explicit.
- Keep pin selection, reference-clock source, and board policy in the target/platform.
- Use canonical imports in implementation code and benches. The package exports
  serve the generator; the central alias table preserves established direct imports.

Follow [LiteX coding style](https://github.com/enjoy-digital/litex/blob/master/doc/coding_style.md)
and the ownership/interface rules in [PHY portability](phy_portability.md).
Parallel PHYs can keep their device-local TX/RX/CRG classes; they do not need a
common superclass merely to share a few assignments or CSR declarations.

## RGMII Receive Errors

RGMII presents RX_DV on the rising RX_CTL sample and RX_DV XOR RX_ER on the
falling sample. Decode these samples after the same alignment and pipeline
stages as their data nibbles. Preserve the existing data, valid and last timing.

Latch an error until the end of its frame, then clear it during idle, as the
Titanium/Trion receiver does. This keeps PHY errors visible to packet-level
consumers after preamble/FCS removal and data-width conversion. At 10/100 Mbps,
an error on either nibble must mark the assembled byte and the rest of the
frame; the invalid cycles between bytes must not clear the latch. Idle RX_ER
indications do not start a frame or contaminate the next frame.

Test error positions at frame boundaries as well as in the payload, and check
that a valid FCS does not override a PHY error. Include a following clean frame
to verify that the error clears.

## Compatibility

For each established import, preserve class identity through the old path,
positional and keyword constructor arguments, stream widths, clock-domain names,
CSR layout and descriptions, and generator YAML names. Compare elaborated vendor
primitive parameters and ports before and after the move. Run the relevant PCS
simulations, wrapper elaboration tests, generator tests, and full CI. Retain
old module paths without a removal date; target builds and third-party
designs often import them directly. Newly introduced modules can move to their
canonical location without retaining transitional paths.

Native BASE-R rates, link capabilities and standalone generation are described
in [Native BASE-R PHYs](baser.md).
