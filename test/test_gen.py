#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import os

def build_config(name):
    errors = 0
    os.system("rm -rf examples/build")
    os.system("cd examples && python3 ../liteeth/gen.py {}.yml".format(name))
    errors += not os.path.isfile("examples/build/gateware/liteeth_core.v")
    os.system("rm -rf examples/build")
    return errors

class TestExamples(unittest.TestCase):
    def test_udp_s7phyrgmii(self):
        errors = build_config("udp_s7phyrgmii")
        self.assertEqual(errors, 0)

    def test_wishbone_mii(self):
        errors = build_config("wishbone_mii")
        self.assertEqual(errors, 0)
