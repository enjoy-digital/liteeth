from liteeth.common import *

from litex.gen.genlib.resetsync import AsyncResetSynchronizer


def converter_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout, packetized=True)


@CEInserter()
class LiteEthPHYRMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = Sink(eth_phy_description(8))

        # # #

        converter = Converter(converter_description(8),
                              converter_description(2))
        self.submodules += converter
        self.comb += [
            converter.sink.stb.eq(sink.stb),
            converter.sink.data.eq(sink.data),
            sink.ack.eq(converter.sink.ack),
            converter.source.ack.eq(1)
        ]
        self.sync += [
            pads.tx_en.eq(converter.source.stb),
            pads.tx_data.eq(converter.source.data)
        ]


@CEInserter()
class LiteEthPHYRMIIRX(Module):
    def __init__(self, pads):
        self.source = source = Source(eth_phy_description(8))

        # # #

        sop = Signal(reset=1)
        sop_set = Signal()
        sop_clr = Signal()
        self.sync += If(sop_set, sop.eq(1)).Elif(sop_clr, sop.eq(0))

        converter = Converter(converter_description(2),
                              converter_description(8))
        converter = ResetInserter()(converter)
        self.submodules += converter

        self.sync += [
            converter.reset.eq(~pads.dv),
            converter.sink.stb.eq(1),
            converter.sink.data.eq(pads.rx_data)
        ]
        self.sync += [
            sop_set.eq(~pads.dv),
            sop_clr.eq(pads.dv)
        ]
        self.comb += [
            converter.sink.sop.eq(sop),
            converter.sink.eop.eq(~pads.dv)
        ]
        self.comb += Record.connect(converter.source, source)


class LiteEthPHYMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset):
        self._reset = CSRStorage()
        self.ref_clk = Signal()

        # # #

        # assumming 100MHz clock provided externally
        self.sync.eth += self.ref_clk.eq(~self.ref_clk)
        self.comb += clock_pads.ref_clk.eq(self.ref_clk)

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.comb += self.cd_eth_rx.clk.eq(ClockSignal("eth"))
        self.comb += self.cd_eth_tx.clk.eq(ClockSignal("eth"))

        if with_hw_init_reset:
            reset = Signal()
            counter_done = Signal()
            self.submodules.counter = counter = Counter(max=512)
            self.comb += [
                counter_done.eq(counter.value == 256),
                counter.ce.eq(~counter_done),
                reset.eq(~counter_done | self._reset.storage)
            ]
        else:
            reset = self._reset.storage
        self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRMII(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        self.dw = 8
        self.submodules.crg = LiteEthPHYMIICRG(clock_pads, pads, with_hw_init_reset)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIITX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIIRX(pads))
        self.comb += [
            self.tx.ce.eq(self.crg.ref_clk == 1),
            self.rx.ce.eq(self.crg.ref_clk == 1)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
