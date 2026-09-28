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
        "a7_1000basex": "serial.basex.a7_gtp",
        "k7_1000basex": "serial.basex.k7_gtx",
        "v7_1000basex": "serial.basex.v7_gth",
        "ku_1000basex": "serial.basex.ku_gth",
        "usp_gth_1000basex": "serial.basex.usp_gth",
        "usp_gty_1000basex": "serial.basex.usp_gty",
        "gw5_1000basex": "serial.basex.gw5",
        "us_lvds_1000basex": "serial.basex.us_lvds",
        "titanium_lvds_1000basex": "serial.basex.titanium_lvds",
    }

    baser_submodules = (
        "pcs_baser.ber_mon", "pcs_baser.block_sync", "pcs_baser.common",
        "pcs_baser.decoder", "pcs_baser.encoder", "pcs_baser.lfsr",
        "pcs_baser.prbs", "pcs_baser.rx", "pcs_baser.scrambler",
        "pcs_baser.tx", "pcs_baser.watchdog",
        "pma_baser.gth_usp", "pma_baser.gtp_7series",
        "pma_baser.gtx_7series", "pma_baser.gty_usp",
    )

    baser_moves = {
        "baser": "serial.baser.diagnostics",
        "a7_gtp_baser": "serial.baser.a7_gtp",
        "k7_gtx_baser": "serial.baser.k7_gtx",
        "us_gt_baser": "serial.baser.usp_gt",
        "pcs_baser": "serial.baser.pcs",
        "pma_baser": "serial.baser.pma",
    }
    for submodule in baser_submodules:
        group, name = submodule.split(".")
        new_group = {"pcs_baser": "pcs", "pma_baser": "pma"}[group]
        baser_moves[submodule] = f"serial.baser.{new_group}.{name}"

    package_exports = {
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
        "a7_gtp_baser":  ("A7_GTP_5G_BASER",),
        "k7_gtx_baser":  ("K7_GTX_5G_BASER", "K7_GTX_10G_BASER"),
        "us_gt_baser":   ("USP_GTH_5G_BASER", "USP_GTH_10G_BASER",
                          "USP_GTY_5G_BASER", "USP_GTY_10G_BASER", "USP_GTY_25G_BASER"),
        "baser":         ("LiteEthBASERPHY",),
        "a7_gtp":        ("QPLL", "QPLLChannel", "QPLLSettings", "GTPTxInit", "GTPRxInit"),
        "pcs_1000basex": ("PCS", "PCSTX", "PCSRX"),
        "pcs_baser":     ("PCS",),
        "pcs_baser.rx":  ("PCSRX",),
        "pcs_baser.tx":  ("PCSTX",),
        "pma_baser":    ("PMA_USP_GTH_10G_BASER", "PMA_USP_GTY_10G_BASER"),
        "pma_baser.gtp_7series": ("PMA_A7_GTP_5G_BASER",),
        "pma_baser.gtx_7series": ("PMA_K7_GTX_5G_BASER", "PMA_K7_GTX_10G_BASER"),
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

    def test_basex_legacy_modules_are_aliases(self):
        for old_name, new_name in self.basex_moves.items():
            with self.subTest(module=old_name):
                old = importlib.import_module(f"liteeth.phy.{old_name}")
                new = importlib.import_module(f"liteeth.phy.{new_name}")
                self.assertIs(old, new)

    def test_direct_import_paths_remain_available(self):
        for module_name, class_names in self.direct_imports.items():
            module = importlib.import_module(f"liteeth.phy.{module_name}")
            for class_name in class_names:
                with self.subTest(module=module_name, name=class_name):
                    self.assertTrue(callable(getattr(module, class_name)))

    def test_baser_submodule_paths_remain_available(self):
        for module_name in self.baser_submodules:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(f"liteeth.phy.{module_name}"))

    def test_baser_legacy_names_are_identical_to_new_names(self):
        for old_name, new_name in self.baser_moves.items():
            old = importlib.import_module(f"liteeth.phy.{old_name}")
            new = importlib.import_module(f"liteeth.phy.{new_name}")
            names = set(self.direct_imports.get(old_name, ()))
            names.update(name for name, value in vars(new).items()
                if callable(value) and getattr(value, "__module__", None) == new.__name__)
            for name in names:
                with self.subTest(module=old_name, name=name):
                    self.assertIs(getattr(old, name), getattr(new, name))

    def test_generator_names_remain_resolvable(self):
        for class_names in self.package_exports.values():
            for class_name in class_names:
                with self.subTest(name=class_name):
                    self.assertTrue(callable(getattr(phy, class_name)))
        self.assertTrue(callable(phy.LiteEthPHY))
