import math

from liteeth.common import *


class LiteEthMACGap(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        gap = math.ceil(eth_interpacket_gap/(dw//8))
        counter = Signal(max=gap, reset_less=True)
        counter_reset = Signal()
        counter_ce = Signal()
        self.sync += \
            If(counter_reset,
               counter.eq(0)
            ).Elif(counter_ce,
                counter.eq(counter + 1)
            )

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            counter_reset.eq(1),
            sink.connect(source),
            If(sink.valid & sink.last & sink.ready,
                NextState("GAP")
            )
        )
        fsm.act("GAP",
            counter_ce.eq(1),
            If(counter == (gap-1),
                NextState("COPY")
            )
        )
