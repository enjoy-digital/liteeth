#
# This file is part of LiteEth.
#
# Copyright (c) 2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import os

root_dir    = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..")
make_script = os.path.join(root_dir, "examples", "make.py")

class TestExamples(unittest.TestCase):
    def example_test(self, t, s):
        os.system("rm -rf {}/build".format(root_dir))
        cmd = "python3 " + make_script + " "
        cmd += "-t {} ".format(t)
        cmd += "-s {} ".format(s)
        cmd += "-p kc705 "
        cmd += "-Ob run False "
        cmd += "build-bitstream"
        os.system(cmd)
        self.assertEqual(os.path.isfile("{}/build/{}_kc705.v".format(root_dir, s.lower())), True)

    def test_base_example(self):
        self.example_test("base", "BaseSoC")
        self.example_test("base", "BaseSoCDevel")

    def test_udp_example(self):
        self.example_test("udp", "UDPSoC")
        self.example_test("udp", "UDPSoCDevel")

    def test_etherbone_example(self):
        self.example_test("etherbone", "EtherboneSoC")
        self.example_test("etherbone", "EtherboneSoCDevel")

    def test_stream_example(self):
        self.example_test("stream", "StreamSoC")
        self.example_test("stream", "StreamSoCDevel")
