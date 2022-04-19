#
# This file is part of LiteEth.
#
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *

# MAC Gap ------------------------------------------------------------------------------------------

class LiteEthAntiUnderflow(Module):
    def __init__(self, dw, depth=32):
        '''
        buffers a whole packet and releases it at once
        workaround for driving PHYs with wide datapaths which expect a
        continuous stream
        '''
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        self.submodules.fifo = fifo = stream.SyncFIFO(
            eth_phy_description(dw),
            depth,
            buffered=True
        )

        self.comb += [
            sink.connect(fifo.sink),
            fifo.source.connect(source, omit=['valid', 'ready'])
        ]

        self.submodules.fsm = fsm = FSM(reset_state="STORE")
        fsm.act("STORE",
            If(sink.valid & sink.last | (fifo.level >= fifo.depth - 1),
                NextState("FLUSH")
            )
        )
        fsm.act("FLUSH",
            fifo.source.connect(source, keep=['valid', 'ready']),
            If(fifo.source.valid & fifo.source.last,
                NextState("STORE")
            )
        )
