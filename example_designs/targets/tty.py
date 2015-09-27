from liteeth.common import *

from targets.base import BaseSoC
from liteeth.frontend.tty import LiteEthTTY


class TTYSoC(BaseSoC):
    default_platform = "kc705"
    def __init__(self, platform):
        BaseSoC.__init__(self, platform,
            mac_address=0x10e2d5000000,
            ip_address="192.168.0.42")
        self.submodules.tty = LiteEthTTY(self.core.udp, convert_ip("192.168.0.14"), 10000)
        self.comb += Record.connect(self.tty.source, self.tty.sink)


class TTYSoCDevel(TTYSoC):
    csr_map = {
        "logic_analyzer":            20
    }
    csr_map.update(TTYSoC.csr_map)
    def __init__(self, platform):
        from litescope.frontend.logic_analyzer import LiteScopeLogicAnalyzer
        from litescope.core.port import LiteScopeTerm
        TTYSoC.__init__(self, platform)
        debug = (
            self.tty.sink.stb,
            self.tty.sink.ack,
            self.tty.sink.data,

            self.tty.source.stb,
            self.tty.source.ack,
            self.tty.source.data
        )
        self.submodules.logic_analyzer = LiteScopeLogicAnalyzer(debug, 4096)
        self.logic_analyzer.trigger.add_port(LiteScopeTerm(self.logic_analyzer.dw))

    def do_exit(self, vns):
        self.logic_analyzer.export(vns, "test/logic_analyzer.csv")

default_subtarget = TTYSoC
