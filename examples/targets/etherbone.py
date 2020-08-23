#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.frontend.etherbone import LiteEthEtherbone

from targets.base import BaseSoC

# EtherboneSoC -------------------------------------------------------------------------------------

class EtherboneSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address=0x10e2d5000000,
            ip_address="192.168.1.50")
        self.submodules.etherbone = LiteEthEtherbone(self.ethcore.udp, 1234, mode="master")
        self.add_wb_master(self.etherbone.wishbone.bus)

# EtherboneSoCDevel --------------------------------------------------------------------------------

class EtherboneSoCDevel(EtherboneSoC):
    def __init__(self, platform):
        from litescope import LiteScopeAnalyzer
        EtherboneSoC.__init__(self, platform)
        debug = [
            # MMAP stream from HOST
            self.etherbone.wishbone.sink.valid,
            self.etherbone.wishbone.sink.last,
            self.etherbone.wishbone.sink.ready,
            self.etherbone.wishbone.sink.we,
            self.etherbone.wishbone.sink.count,
            self.etherbone.wishbone.sink.base_addr,
            self.etherbone.wishbone.sink.be,
            self.etherbone.wishbone.sink.addr,
            self.etherbone.wishbone.sink.data,

            # MMAP stream to HOST
            self.etherbone.wishbone.source.valid,
            self.etherbone.wishbone.source.last,
            self.etherbone.wishbone.source.ready,
            self.etherbone.wishbone.source.we,
            self.etherbone.wishbone.source.count,
            self.etherbone.wishbone.source.base_addr,
            self.etherbone.wishbone.source.be,
            self.etherbone.wishbone.source.addr,
            self.etherbone.wishbone.source.data,

            # Etherbone wishbone master
            self.etherbone.wishbone.bus.dat_w,
            self.etherbone.wishbone.bus.dat_r,
            self.etherbone.wishbone.bus.adr,
            self.etherbone.wishbone.bus.sel,
            self.etherbone.wishbone.bus.cyc,
            self.etherbone.wishbone.bus.stb,
            self.etherbone.wishbone.bus.ack,
            self.etherbone.wishbone.bus.we,
            self.etherbone.wishbone.bus.cti,
            self.etherbone.wishbone.bus.bte,
            self.etherbone.wishbone.bus.err
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(debug, 4096, csr_csv="test/analyzer.csv")
        self.add_csr("analyzer")

default_subtarget = EtherboneSoC
