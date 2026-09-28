# PHY Layout and Compatibility

The PHY tree has two jobs: expose PHY classes to targets and the core
generator, and implement the electrical interface or Ethernet line code.
Established public imports remain available when implementation files move.

| PHY group | Current implementation | Public entry points |
| --- | --- | --- |
| MII, RMII, GMII, XGMII | `liteeth/phy/{mii,rmii,gmii,gmii_mii,xgmii}.py` | `liteeth.phy` and the individual modules |
| Vendor RGMII | `liteeth/phy/parallel/rgmii/` | Original vendor module names and the classes in `liteeth.phy` |
| 1000/2500BASE-X | `serial/basex/pcs.py` and device adapters | Original module names and the classes in `liteeth.phy` |
| 5/10/25GBASE-R | `serial/baser/` with `pcs/`, `pma/`, diagnostics, and device wrappers | Canonical `liteeth.phy.serial.baser.*` imports |
| GTP initialization | `a7_gtp.py` | Direct imports from targets and BASE-R PMAs |

The standalone generator selects a PHY by looking up its YAML `phy` value in
`liteeth.phy`. These names, the `LiteEthPHY` autodetection function, and the
established BASE-X and RGMII direct module imports must remain available during
a reorganization. BASE-R was recently introduced, so its former top-level
`baser`, `pcs_baser`, `pma_baser`, and device wrapper paths are not retained.

Serial Ethernet modes are grouped under `liteeth/phy/serial/`. The 64b/66b
BASE-R PCS, PMAs, diagnostics, and wrappers live in `baser/`; the 8b/10b
BASE-X PCS and device adapters live in `basex/`. The established BASE-X module
names are registered as aliases in `liteeth.phy.__init__`, so code that patches
a module-level PCS helper still affects the class implementation.
Vendor RGMII adapters are grouped under `liteeth/phy/parallel/rgmii/`, with
their original module names registered in the same alias table. The MII, RMII,
GMII, and XGMII entry points stay at the top level: each already has a protocol-specific name
and direct users in LiteX or LiteEth. Keep device-specific primitive parameters
visible; sharing a PLL or reset helper does not imply that two line codes
share a PMA.

For each established import, preserve class identity through the old path,
positional and keyword constructor arguments, stream widths, clock-domain names,
CSR layout and descriptions, and generator YAML names. Compare elaborated vendor
primitive parameters and ports before and after the move. Run the relevant PCS
simulations, wrapper elaboration tests, generator tests, and full CI. Retain
old module paths without a removal date; target builds and third-party
designs often import them directly. Newly introduced modules can move to their
canonical location without retaining transitional paths.
