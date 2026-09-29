#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import subprocess

from test.test_gen import generate_config


class TestBASERGenerator(unittest.TestCase):
    def test_supported_phys(self):
        for phy, channel, common, buffer in [
            ('K7_GTX_5G_BASER',  'GTXE2_CHANNEL', 'GTXE2_COMMON', 'IBUFDS_GTE2'),
            ('K7_GTX_10G_BASER', 'GTXE2_CHANNEL', 'GTXE2_COMMON', 'IBUFDS_GTE2'),
            ('USP_GTH_5G_BASER', 'GTHE4_CHANNEL', 'GTHE4_COMMON', 'IBUFDS_GTE4'),
            ('USP_GTH_10G_BASER','GTHE4_CHANNEL', 'GTHE4_COMMON', 'IBUFDS_GTE4'),
            ('USP_GTY_5G_BASER', 'GTYE4_CHANNEL', 'GTYE4_COMMON', 'IBUFDS_GTE4'),
            ('USP_GTY_10G_BASER','GTYE4_CHANNEL', 'GTYE4_COMMON', 'IBUFDS_GTE4'),
            ('USP_GTY_25G_BASER','GTYE4_CHANNEL', 'GTYE4_COMMON', 'IBUFDS_GTE4'),
        ]:
            with self.subTest(phy=phy):
                rtl = generate_config('udp_baser', phy=phy)
                for primitive in [channel, common, buffer]:
                    self.assertIn(primitive, rtl)
                for port in ['baser_refclk_p', 'baser_refclk_n', 'baser_txp', 'baser_rxp',
                             'baser_rst', 'baser_link_up', 'udp0_sink_keep', 'udp0_source_keep']:
                    self.assertIn(port, rtl)

    def test_25g_integer_reference(self):
        rtl = generate_config('udp_baser', phy='USP_GTY_25G_BASER', refclk_freq=161.1328125e6)
        self.assertIn('GTYE4_COMMON', rtl)

    def test_unsupported_modes_are_rejected(self):
        for options in [dict(phy_fec='rs'), dict(refclk_from_fabric=True)]:
            with self.subTest(options=options), self.assertRaises(subprocess.CalledProcessError):
                generate_config('udp_baser', **options)
