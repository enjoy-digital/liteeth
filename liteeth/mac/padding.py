import math

from liteeth.common import *


class LiteEthMACPaddingInserter(Module):
    def __init__(self, dw, padding):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        padding_limit = math.ceil(padding/(dw/8))-1

        counter = Signal(16, reset=1)
        counter_done = Signal()
        counter_reset = Signal()
        counter_ce = Signal()
        self.sync += \
            If(counter_reset,
                counter.eq(0)
            ).Elif(counter_ce,
                counter.eq(counter + 1)
            )
        self.comb += counter_done.eq(counter >= padding_limit)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.connect(source),
            If(source.valid & source.ready,
                counter_ce.eq(1),
                If(sink.last,
                    If(~counter_done,
                        source.last.eq(0),
                        NextState("PADDING")
                    ).Else(
                        counter_reset.eq(1)
                    )
                )
            )
        )
        fsm.act("PADDING",
            source.valid.eq(1),
            source.last.eq(counter_done),
            source.data.eq(0),
            If(source.valid & source.ready,
                counter_ce.eq(1),
                If(counter_done,
                    counter_reset.eq(1),
                    NextState("IDLE")
                )
            )
        )


class LiteEthMACPaddingChecker(Module):
    def __init__(self, dw, packet_min_length):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        # TODO: see if we should drop the packet when
        # payload size < minimum ethernet payload size
        self.comb += sink.connect(source)

