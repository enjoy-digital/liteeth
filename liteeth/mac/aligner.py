# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# This file is Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# License: BSD

import math

from liteeth.common import *

class LiteEthMACAligner(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #
        cw = dw // 8
        self.sync += [
            sink.connect(source),
            If(source.valid & source.ready,
                If(sink.last & (sink.last_be != cw),
                   source.last_be.eq(cw)
                )
            )
        ]
