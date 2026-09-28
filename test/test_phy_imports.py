#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import importlib
import unittest

import liteeth.phy as phy


# Public PHY Imports -------------------------------------------------------------------------------

class TestPHYImports(unittest.TestCase):
    remaining_moves = {
        "mii":      "parallel.mii",
        "rmii":     "parallel.rmii",
        "gmii":     "parallel.gmii",
        "gmii_mii": "parallel.gmii_mii",
        "xgmii":    "parallel.xgmii",
        "model":    "simulation.model",
        "a7_gtp":   "serial.gtp_7series",
    }

    rgmii_moves = {
        "s6rgmii": "parallel.rgmii.s6",
        "s7rgmii": "parallel.rgmii.s7",
        "usrgmii": "parallel.rgmii.us",
        "ecp5rgmii": "parallel.rgmii.ecp5",
        "gw5rgmii": "parallel.rgmii.gw5",
        "titaniumrgmii": "parallel.rgmii.titanium",
        "trionrgmii": "parallel.rgmii.trion",
        "agilex_rgmii": "parallel.rgmii.agilex",
    }

    rgmii_exports = {
        "s6rgmii": "LiteEthS6PHYRGMII",
        "s7rgmii": "LiteEthS7PHYRGMII",
        "usrgmii": "LiteEthUSPHYRGMII",
        "ecp5rgmii": "LiteEthECP5PHYRGMII",
        "agilex_rgmii": "LiteEthAgilexPHYRGMII",
    }

    basex_moves = {
        "pcs_1000basex": "serial.basex.pcs",
        "a7_1000basex": "serial.basex.wrappers.a7_gtp",
        "k7_1000basex": "serial.basex.wrappers.k7_gtx",
        "v7_1000basex": "serial.basex.wrappers.v7_gth",
        "ku_1000basex": "serial.basex.wrappers.ku_gth",
        "usp_gth_1000basex": "serial.basex.wrappers.usp_gth",
        "usp_gty_1000basex": "serial.basex.wrappers.usp_gty",
        "gw5_1000basex": "serial.basex.wrappers.gw5",
        "us_lvds_1000basex": "serial.basex.wrappers.us_lvds",
        "titanium_lvds_1000basex": "serial.basex.wrappers.titanium_lvds",
    }

    package_exports = {
        "mii":                ("LiteEthPHYMII",),
        "rmii":               ("LiteEthPHYRMII",),
        "gmii":               ("LiteEthPHYGMII",),
        "gmii_mii":           ("LiteEthPHYGMIIMII",),
        "xgmii":              ("LiteEthPHYXGMII",),
        "a7_1000basex":      ("A7_1000BASEX", "A7_2500BASEX"),
        "k7_1000basex":      ("K7_1000BASEX", "K7_2500BASEX"),
        "ku_1000basex":      ("KU_1000BASEX", "KU_2500BASEX"),
        "usp_gth_1000basex": ("USP_GTH_1000BASEX", "USP_GTH_2500BASEX"),
        "usp_gty_1000basex": ("USP_GTY_1000BASEX", "USP_GTY_2500BASEX"),
        "us_lvds_1000basex": ("US_LVDS_1000BASEX",),
    }

    direct_imports = {
        "v7_1000basex":  ("V7_1000BASEX", "V7_2500BASEX"),
        "gw5_1000basex": ("GW5_1000BASEX",),
        "a7_gtp":        ("QPLL", "QPLLChannel", "QPLLSettings", "GTPTxInit", "GTPRxInit"),
        "model":         ("LiteEthPHYModel",),
        "pcs_1000basex": ("PCS", "PCSTX", "PCSRX"),
    }

    baser_imports = {
        "serial.baser.wrappers.diagnostics": ("LiteEthBASERPHY",),
        "serial.baser.wrappers.a7_gtp":    ("A7_GTP_5G_BASER",),
        "serial.baser.wrappers.k7_gtx":    ("K7_GTX_5G_BASER", "K7_GTX_10G_BASER"),
        "serial.baser.wrappers.usp_gt":    ("USP_GTH_5G_BASER", "USP_GTH_10G_BASER",
                                   "USP_GTY_5G_BASER", "USP_GTY_10G_BASER", "USP_GTY_25G_BASER"),
        "serial.baser.pcs":       ("PCS",),
        "serial.baser.pcs.rx":    ("PCSRX",),
        "serial.baser.pcs.tx":    ("PCSTX",),
        "serial.baser.pma":       ("PMA_USP_GTH_10G_BASER", "PMA_USP_GTY_10G_BASER"),
        "serial.baser.pma.gtp_7series": ("PMA_A7_GTP_5G_BASER",),
        "serial.baser.pma.gtx_7series": ("PMA_K7_GTX_5G_BASER", "PMA_K7_GTX_10G_BASER"),
    }

    def test_package_exports_match_legacy_modules(self):
        for module_name, class_names in self.package_exports.items():
            module = importlib.import_module(f"liteeth.phy.{module_name}")
            for class_name in class_names:
                with self.subTest(module=module_name, name=class_name):
                    self.assertIs(getattr(phy, class_name), getattr(module, class_name))

    def test_rgmii_package_exports_match_legacy_modules(self):
        for module_name, export_name in self.rgmii_exports.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(f"liteeth.phy.{module_name}")
                self.assertIs(getattr(phy, export_name), module.LiteEthPHYRGMII)

    def test_rgmii_legacy_modules_are_aliases(self):
        for old_name, new_name in self.rgmii_moves.items():
            with self.subTest(module=old_name):
                old = importlib.import_module(f"liteeth.phy.{old_name}")
                new = importlib.import_module(f"liteeth.phy.{new_name}")
                self.assertIs(old, new)
                self.assertIs(getattr(phy, old_name), new)

    def test_remaining_legacy_modules_are_aliases(self):
        for old_name, new_name in self.remaining_moves.items():
            with self.subTest(module=old_name):
                old = importlib.import_module(f"liteeth.phy.{old_name}")
                new = importlib.import_module(f"liteeth.phy.{new_name}")
                self.assertIs(old, new)
                self.assertIs(getattr(phy, old_name), new)

    def test_basex_legacy_modules_are_aliases(self):
        for old_name, new_name in self.basex_moves.items():
            with self.subTest(module=old_name):
                old = importlib.import_module(f"liteeth.phy.{old_name}")
                new = importlib.import_module(f"liteeth.phy.{new_name}")
                self.assertIs(old, new)
                self.assertIs(getattr(phy, old_name), new)

    def test_basex_device_helpers_keep_their_imports(self):
        helpers = {
            "gw5" : ("GW5SerDes",),
            "us_lvds" : ("COMMA_RD_N", "COMMA_RD_P", "USLVDSClocking", "USLVDSTXGearbox",
                "USLVDSSerdesTX", "USLVDSPhaseDetector", "USLVDSCommaAligner",
                "USLVDSRXGearbox", "USLVDSSerdesRX"),
            "titanium_lvds" : ("EfinixSerdesDiffTx", "EfinixSerdesDiffRx", "Decoder8b10bChecker",
                "Decoder8b10bIdleChecker", "EfinixAligner", "EfinixSerdesBuffer",
                "EfinixSerdesDiffRxClockRecovery", "EfinixSerdesClocking"),
        }
        for device, names in helpers.items():
            wrapper = importlib.import_module(f"liteeth.phy.serial.basex.wrappers.{device}")
            pma = importlib.import_module(f"liteeth.phy.serial.basex.pma.{device}")
            for name in names:
                with self.subTest(device=device, name=name):
                    self.assertIs(getattr(wrapper, name), getattr(pma, name))
        from liteeth.phy.serial.basex.pma.gw5_config import _serdes_csr, _serdes_toml
        self.assertIs(phy.gw5_1000basex._serdes_csr, _serdes_csr)
        self.assertIs(phy.gw5_1000basex._serdes_toml, _serdes_toml)

    def test_direct_import_paths_remain_available(self):
        for module_name, class_names in self.direct_imports.items():
            module = importlib.import_module(f"liteeth.phy.{module_name}")
            for class_name in class_names:
                with self.subTest(module=module_name, name=class_name):
                    self.assertTrue(callable(getattr(module, class_name)))

    def test_baser_canonical_modules_are_available(self):
        for module_name, class_names in self.baser_imports.items():
            module = importlib.import_module(f"liteeth.phy.{module_name}")
            for class_name in class_names:
                with self.subTest(module=module_name, name=class_name):
                    self.assertTrue(callable(getattr(module, class_name)))

    def test_generator_names_remain_resolvable(self):
        for class_names in self.package_exports.values():
            for class_name in class_names:
                with self.subTest(name=class_name):
                    self.assertTrue(callable(getattr(phy, class_name)))
        self.assertTrue(callable(phy.LiteEthPHY))
