# PHY Layout and Compatibility

The PHY tree has two jobs: expose stable PHY classes to targets and the core
generator, and implement the electrical interface or Ethernet line code. The
public import paths are part of the integration interface. Keep them working
when implementation files move.

| PHY group | Current implementation | Public entry points |
| --- | --- | --- |
| MII, RMII, GMII, XGMII | `liteeth/phy/{mii,rmii,gmii,gmii_mii,xgmii}.py` | `liteeth.phy` and the individual modules |
| Vendor RGMII | `liteeth/phy/*rgmii.py` | Individual modules and the aliases in `liteeth.phy` |
| 1000/2500BASE-X | `pcs_1000basex.py` and device-named wrapper modules | Device modules and the aliases in `liteeth.phy` |
| 5/10/25GBASE-R | `pcs_baser/`, `pma_baser/`, `baser.py`, and device-named wrappers | Every existing module and submodule path |
| GTP initialization | `a7_gtp.py` | Direct imports from targets and BASE-R PMAs |

The standalone generator selects a PHY by looking up its YAML `phy` value in
`liteeth.phy`. These names, the `LiteEthPHY` autodetection function, and the
existing direct module imports must remain available during a reorganization.
The existing `pcs_baser.*` and `pma_baser.*` submodule paths matter as well as
their package-level imports.

The intended implementation layout groups serial Ethernet modes under
`liteeth/phy/serial/`: `basex/` for 8b/10b PCS and its device adapters, and
`baser/` for 64b/66b PCS, PMAs, diagnostics, and wrappers. Existing top-level
modules remain small forwarding modules. Parallel and RGMII PHYs can be
grouped separately after the serial modes are stable. Keep device-specific
primitive parameters visible; sharing a PLL or reset helper does not imply
that two line codes share a PMA.

For each move, preserve class identity through the old import path, positional
and keyword constructor arguments, stream widths, clock-domain names, CSR
layout and descriptions, and generator YAML names. Compare elaborated vendor
primitive parameters and ports before and after the move. Run the relevant PCS
simulations, wrapper elaboration tests, generator tests, and full CI. Retain
old module paths without a removal date; target builds and third-party
designs often import them directly.
