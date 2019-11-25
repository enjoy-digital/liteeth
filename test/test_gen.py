# This file is Copyright (c) 2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import unittest
import os

root_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..")

class TestGen(unittest.TestCase):
    def test(self):
        for phy in ["mii", "gmii", "rgmii"]:
            for core in ["wishbone", "udp"]:
                os.system("rm -rf {}/build".format(root_dir))
                os.system("python3 {}/liteeth/gen.py --phy={} --core={}".format(root_dir, phy, core))
                self.assertEqual(os.path.isfile("{}/build/gateware/liteeth_core.v".format(root_dir)), True)
