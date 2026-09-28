import importlib as _importlib
import sys as _sys

from liteeth.common import *


# PHY Autodetection --------------------------------------------------------------------------------

def LiteEthPHY(clock_pads, pads, clk_freq=None, **kwargs):
    # Autodetect PHY
    if hasattr(clock_pads, "gtx") and len(pads.tx_data) == 8:
        if hasattr(clock_pads, "tx"):
            # This is a 10/100/1G PHY
            from liteeth.phy.parallel.gmii_mii import LiteEthPHYGMIIMII
            return LiteEthPHYGMIIMII(clock_pads, pads, clk_freq=clk_freq, **kwargs)
        else:
            # This is a pure 1G PHY
            from liteeth.phy.parallel.gmii import LiteEthPHYGMII
            return LiteEthPHYGMII(clock_pads, pads, **kwargs)
    elif hasattr(pads, "rx_ctl"):
        # This is a 10/100/1G RGMII PHY
        raise ValueError("RGMII PHYs are specific to vendors (for now), use direct instantiation")
    elif len(pads.tx_data) == 4:
        # This is a MII PHY
        from liteeth.phy.parallel.mii import LiteEthPHYMII
        return LiteEthPHYMII(clock_pads, pads, **kwargs)
    else:
        raise ValueError("Unable to autodetect PHY from platform file, use direct instantiation")


# Public PHY Classes -------------------------------------------------------------------------------

from liteeth.phy.parallel.mii      import LiteEthPHYMII
from liteeth.phy.parallel.rmii     import LiteEthPHYRMII
from liteeth.phy.parallel.gmii     import LiteEthPHYGMII
from liteeth.phy.parallel.gmii_mii import LiteEthPHYGMIIMII
from liteeth.phy.parallel.xgmii    import LiteEthPHYXGMII

from liteeth.phy.parallel.rgmii.s6     import LiteEthPHYRGMII as LiteEthS6PHYRGMII
from liteeth.phy.parallel.rgmii.s7     import LiteEthPHYRGMII as LiteEthS7PHYRGMII
from liteeth.phy.parallel.rgmii.us     import LiteEthPHYRGMII as LiteEthUSPHYRGMII
from liteeth.phy.parallel.rgmii.ecp5   import LiteEthPHYRGMII as LiteEthECP5PHYRGMII
from liteeth.phy.parallel.rgmii.agilex import LiteEthPHYRGMII as LiteEthAgilexPHYRGMII

from liteeth.phy.serial.basex.wrappers.a7_gtp  import A7_1000BASEX
from liteeth.phy.serial.basex.wrappers.a7_gtp  import A7_2500BASEX
from liteeth.phy.serial.basex.wrappers.k7_gtx  import K7_1000BASEX
from liteeth.phy.serial.basex.wrappers.k7_gtx  import K7_2500BASEX
from liteeth.phy.serial.basex.wrappers.ku_gth  import KU_1000BASEX
from liteeth.phy.serial.basex.wrappers.ku_gth  import KU_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gth import USP_GTH_1000BASEX
from liteeth.phy.serial.basex.wrappers.usp_gth import USP_GTH_2500BASEX
from liteeth.phy.serial.basex.wrappers.usp_gty import USP_GTY_1000BASEX
from liteeth.phy.serial.basex.wrappers.usp_gty import USP_GTY_2500BASEX
from liteeth.phy.serial.basex.wrappers.us_lvds import US_LVDS_1000BASEX

# Legacy Module Imports ----------------------------------------------------------------------------

# Keep established direct imports pointing at the implementation modules. Registering
# the modules themselves also preserves module-level patching by downstream targets.
_legacy_modules = {
    "mii"                     : "parallel.mii",
    "rmii"                    : "parallel.rmii",
    "gmii"                    : "parallel.gmii",
    "gmii_mii"                : "parallel.gmii_mii",
    "xgmii"                   : "parallel.xgmii",
    "model"                   : "simulation.model",
    "a7_gtp"                  : "serial.gtp_7series",
    "pcs_1000basex"           : "serial.basex.pcs",
    "a7_1000basex"            : "serial.basex.wrappers.a7_gtp",
    "k7_1000basex"            : "serial.basex.wrappers.k7_gtx",
    "v7_1000basex"            : "serial.basex.wrappers.v7_gth",
    "ku_1000basex"            : "serial.basex.wrappers.ku_gth",
    "usp_gth_1000basex"       : "serial.basex.wrappers.usp_gth",
    "usp_gty_1000basex"       : "serial.basex.wrappers.usp_gty",
    "gw5_1000basex"           : "serial.basex.wrappers.gw5",
    "us_lvds_1000basex"       : "serial.basex.wrappers.us_lvds",
    "titanium_lvds_1000basex" : "serial.basex.wrappers.titanium_lvds",
    "s6rgmii"                 : "parallel.rgmii.s6",
    "s7rgmii"                 : "parallel.rgmii.s7",
    "usrgmii"                 : "parallel.rgmii.us",
    "ecp5rgmii"               : "parallel.rgmii.ecp5",
    "gw5rgmii"                : "parallel.rgmii.gw5",
    "titaniumrgmii"           : "parallel.rgmii.titanium",
    "trionrgmii"              : "parallel.rgmii.trion",
    "agilex_rgmii"            : "parallel.rgmii.agilex",
}
for _legacy_name, _module_name in _legacy_modules.items():
    _module = _importlib.import_module(f"{__name__}.{_module_name}")
    _sys.modules[f"{__name__}.{_legacy_name}"] = _module
    globals()[_legacy_name] = _module
del _legacy_name, _module_name, _module
