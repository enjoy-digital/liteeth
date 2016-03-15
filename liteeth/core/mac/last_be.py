from liteeth.common import *


class LiteEthMACTXLastBE(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        ongoing = Signal(reset=1)
        self.sync += \
            If(sink.stb & sink.ack,
                If(sink.eop,
                    ongoing.eq(1)
                ).Elif(sink.last_be,
                    ongoing.eq(0)
                )
            )
        self.comb += [
            source.stb.eq(sink.stb & ongoing),
            source.eop.eq(sink.last_be),
            source.data.eq(sink.data),
            sink.ack.eq(source.ack)
        ]


class LiteEthMACRXLastBE(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        self.comb += [
            sink.connect(source),
            source.last_be.eq(sink.eop)
        ]
