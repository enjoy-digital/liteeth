from migen import Module
from liteeth.common import *

from functools import reduce
from operator import or_


IDLE, START, TERMINATE = Signal(8, reset=0x07), Signal(8, reset=0xFB), Signal(8, reset=0xFD)


class LiteEthPHYXGMIITX(Module):
    def __init__(self, pads, dw):
        cw = dw // 8
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        # Assume we can always take the data
        self.comb += sink.ready.eq(1)

        edge_terminated = Signal()
        valid_buf = Signal()
        self.sync += valid_buf.eq(sink.valid)

        ctl_ones = int('1'*cw, 2)
        ctl_character_data = int('00000111'*cw, 2)  # IDLE

        sig = [
            If(sink.last_be >= (1 << i),
               pads.tx_ctl[i+1].eq(0),
               pads.tx_data[8*i: 8*(i+1)].eq(sink.data[8*i:8*(i+1)])
            ).Elif(sink.last_be << 1 == (1 << i),
               pads.tx_ctl[i+1].eq(1),
               pads.tx_data[8*i: 8*(i+1)].eq(TERMINATE)
            ).Else(
               pads.tx_ctl[i+1].eq(1),
               pads.tx_data[8*i: 8*(i+1)].eq(IDLE)
            )
            for i in range(cw - 1)
        ]

        all_but_one_idles = [IDLE for _ in range(cw-1)]
        # TODO: This is best rewritten as an FSM
        self.sync += [
            If((sink.valid ^ valid_buf) & sink.valid,  # First word
               pads.tx_ctl.eq(1),
               pads.tx_data.eq(Cat(START, sink.data[8: 8*cw]))
            ).Elif(sink.valid & ~sink.last,  # Between the frame
                   pads.tx_ctl.eq(0),
                   pads.tx_data.eq(sink.data)
            ).Elif(sink.valid & sink.last & (sink.last_be != (1 << (cw - 1))),  # Last word
                   pads.tx_ctl[cw-1].eq(1),
                   pads.tx_data[8*(cw-1):].eq(IDLE),
                   *sig
            ).Elif(sink.valid & sink.last & (sink.last_be == (1 << (cw - 1))),  # Last word
                   pads.tx_ctl.eq(0),
                   pads.tx_data.eq(sink.data),
                   edge_terminated.eq(1)
            ).Elif(edge_terminated,
                   edge_terminated.eq(0),
                   pads.tx_ctl.eq(ctl_ones),
                   pads.tx_data.eq(Cat(TERMINATE, *all_but_one_idles))
            ).Else(
                pads.tx_ctl.eq(ctl_ones),
                pads.tx_data.eq(ctl_character_data),
            )
        ]


PREAMBLE_START=Signal(8, reset=0x55)

class LiteEthPHYXGMIIRX(Module):
    def __init__(self, pads, dw):
        cw = dw // 8
        self.source = source = stream.Endpoint(eth_phy_description(dw))
        rx_ctl_d = Signal(len(pads.rx_ctl))
        rx_data_d = Signal(len(pads.rx_data))
        self.sync += [rx_ctl_d.eq(pads.rx_ctl),
                      rx_data_d.eq(pads.rx_data)
        ]

        start = Signal()
        self.comb += start.eq((rx_ctl_d == 0x1) & (rx_data_d[0:8] == START))

        terminate, last_be = Signal(cw, reset=0), Signal(cw, reset=0)
        self.comb += [terminate[i].eq(rx_ctl_d[i] &
                                      (rx_data_d[8*i: 8*(i+1)] == TERMINATE))
                      for i in range(cw)]
        self.comb += last_be.eq(terminate >> 1)

        end, end_d = Signal(), Signal()
        self.comb += end.eq(((pads.rx_ctl == 0xFF) & (pads.rx_data[0:8] == TERMINATE)) |
                            reduce(or_, last_be))
        self.sync += end_d.eq(end)

        self.sync += [
            source.last_be.eq(last_be),
            If(start,
               source.valid.eq(1),
               source.data.eq(Cat(PREAMBLE_START, rx_data_d[8:])),
               source.first.eq(1),
               source.last.eq(0)
            ).Elif(end,
                   source.data.eq(rx_data_d),
                   source.first.eq(0),
                   source.last.eq(1)
            ).Elif(end_d,
                   source.valid.eq(0),
                   source.last.eq(0),
            ).Else(source.data.eq(rx_data_d),
                   source.last.eq(0),
                   source.first.eq(0),
            )
        ]
        # self.sync += [source.error[i].eq(pads.rx_ctl[i] &
        #                                  pads.rx_data[8*i: 8*(i+1)])
        #               for i in range(cw)]


class LiteEthPHYXGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, model=False):
        self._reset = CSRStorage()
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()
        if model:
            self.comb += [self.cd_eth_rx.clk.eq(ClockSignal()),
                          self.cd_eth_tx.clk.eq(ClockSignal())
            ]
        else:
            self.comb += [self.cd_eth_rx.clk.eq(clock_pads.rx),
                          self.cd_eth_tx.clk.eq(clock_pads.tx)
            ]


class LiteEthPHYXGMII(Module, AutoCSR):
    def __init__(self, clock_pads, pads, model=False, dw=64, with_hw_init_reset=True):
        self.dw = dw
        self.submodules.crg = LiteEthPHYXGMIICRG(clock_pads, model)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(
            LiteEthPHYXGMIITX(pads, self.dw))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(
            LiteEthPHYXGMIIRX(pads, self.dw))
        self.sink, self.source = self.tx.sink, self.rx.source
