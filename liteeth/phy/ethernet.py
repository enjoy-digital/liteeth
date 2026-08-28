#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2021-2022 Charles-Henri Mousset <ch.mousset@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.misc import WaitTimer

from litex.soc.integration.doc import ModuleDoc
from litex.build.io import DifferentialInput

from liteeth.common import *
from liteeth.phy.common import *


def converter_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)


symbol_description = [("data", 2)]
SYMBOL_FALLING     = 0b01
SYMBOL_RISING      = 0b10
SYMBOL_NL          = 0b00
SYMBOL_NH          = 0b11


class LiteEthPHYETHERNETTXBit(Module):
    def __init__(self, tx_o, tx_oe, period_2):
        # inputs
        self.sink = stream.Endpoint(symbol_description)

        # # #

        # buffer input (improves timing)
        sink = stream.Endpoint(symbol_description)
        self.sync += [
            If(sink.valid,
                If(sink.ready,
                    sink.valid.eq(0),
                    self.sink.ready.eq(1),
                ),
            ).Else(
                If(self.sink.ready,
                    If(self.sink.valid,
                        sink.valid.eq(1),
                        sink.data.eq(self.sink.data),
                        self.sink.ready.eq(0),
                    )
                ).Else(
                    self.sink.ready.eq(1),
                ),
            )
        ]

        # output signal
        tx = Signal()
        txe = Signal()
        self.sync += [
            tx_o.eq(tx),
            tx_oe.eq(txe),
        ]
        nibble_1 = Signal()
        nibble_2 = Signal()
        self.submodules.wt = wt = WaitTimer(period_2 - 1)
        self.submodules.fsm = fsm = FSM("IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                NextValue(nibble_1, sink.data[0]),
                NextValue(nibble_2, sink.data[1]),
                NextState("NIBBLE_1"),
            ),
        )
        fsm.act("NIBBLE_1",
            wt.wait.eq(~wt.done),
            txe.eq(1),
            tx.eq(nibble_1),
            If(wt.done,
                NextState("NIBBLE_2"),
            ),
        )
        fsm.act("NIBBLE_2",
            wt.wait.eq(~wt.done),
            txe.eq(1),
            tx.eq(nibble_2),
            If(wt.done,
                sink.ready.eq(1),
                If(sink.valid,
                    NextValue(nibble_1, sink.data[0]),
                    NextValue(nibble_2, sink.data[1]),
                    NextState("NIBBLE_1"),
                ).Else(
                    NextState("IDLE"),
                ),
            ),
        )


class LiteEthPHYETHERNETTX(Module):
    def __init__(self, pads, sys_clk_freq, period_2):
        # Inputs
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        # Deserializing
        converter = stream.StrideConverter(converter_description(8),
                                           converter_description(1))
        self.submodules += converter
        self.comb += sink.connect(converter.sink, omit=["last_be", "error"])

        # Output logic
        tx = Signal()
        txe = Signal()
        if hasattr(pads, "tx") and hasattr(pads, "txe"):
            # Differential transmission handled externally
            self.comb += [pads.tx.eq(tx), pads.txe.eq(txe)]
        else:
            self.specials += Tristate(pads.td_p, tx, txe)
            self.specials += Tristate(pads.td_n, ~tx, txe)

        # Manchester encoding
        self.submodules.tx_bit = bit_tx = ClockDomainsRenamer("eth_tx")(
            LiteEthPHYETHERNETTXBit(tx, txe, period_2))
        self.submodules.fifo = fifo = ClockDomainsRenamer({"write": "sys", "read": "eth_tx"})(
            stream.AsyncFIFO(symbol_description, 4))
        self.comb += fifo.source.connect(bit_tx.sink)

        # Normal Link Pulse generation timer
        NLP_PERIOD = int(sys_clk_freq / 1000 * 16)  # 16ms
        self.submodules.nlp_wt = nlp_wt = WaitTimer(NLP_PERIOD)

        # Main FSM
        self.submodules.fsm = fsm = FSM("IDLE")
        fsm.act("IDLE",
            # send the Normal Link Pulses
            nlp_wt.wait.eq(1),
            If(nlp_wt.done,
                NextState("NLP1"),
            ),
            If(converter.source.valid,
                NextState("TX"),
            ),
        )
        fsm.act("NLP1",
            fifo.sink.data.eq(SYMBOL_NH),
            fifo.sink.valid.eq(1),
            If(fifo.sink.ready,
                NextState("NLP2"),
            ),
        )
        fsm.act("NLP2",
            fifo.sink.data.eq(SYMBOL_NL),
            fifo.sink.valid.eq(1),
            If(fifo.sink.ready,
                NextState("IDLE"),
            ),
        )
        fsm.act("TX",
            converter.source.connect(fifo.sink, omit=["data"]),
            If(converter.source.data,
                fifo.sink.data.eq(SYMBOL_RISING),
            ).Else(
                fifo.sink.data.eq(SYMBOL_FALLING),
            ),
            If(~converter.source.valid,
                NextState("IDLE"),
            ),
        )


class ManchesterReceiver(Module):
    def __init__(self, period_2):
        # Parameters
        self.period_2 = period_2
        self.period = period_2 * 2

        # Inputs
        self.rx = rx = Signal()

        # Outputs
        self.source = stream.Endpoint([("data", 1)])
        self.in_sync = Signal()

        # # #

        # receive shift register and edge detection
        source = stream.Endpoint([("data", 1)])
        sr = Signal(4)
        rx_dff = Signal(3)
        match_rising = Signal()
        match_falling = Signal()
        self.comb += [
            sr.eq(Cat(rx, rx_dff)),
        ]
        self.comb += [
            Case(sr, {
                0b0011: [
                    match_rising.eq(1),
                    source.data.eq(1),
                ],
                0b1100: [
                    match_falling.eq(1),
                ],
            }),
        ]
        self.sync += rx_dff.eq(sr)

        # edge synchronization
        self.submodules.wt_bit = wt_bit = WaitTimer(self.period - 2)
        self.submodules.wt_edge = wt_edge = WaitTimer(2)  # valid edge is at period +- 1 clk cycle
        self.submodules.fsm = fsm = FSM("WAIT_EDGE")
        fsm.act("WAIT_EDGE",
            wt_edge.wait.eq(1),
            If(match_rising | match_falling,
                source.valid.eq(1),
                NextState("IN_BIT"),
            ),
        )
        fsm.act("IN_BIT",
            wt_bit.wait.eq(1),
            If(wt_bit.done,
                NextState("WAIT_EDGE"),
            ),
        )

        # output stream buffer (improve timing)
        # also fuse last valid data bit and end of frame detection
        data = Signal()
        self.sync += [
            self.source.valid.eq(source.valid),
            self.source.data.eq(data),
            If(source.valid,
                self.in_sync.eq(1),
                self.source.last.eq(0),
                data.eq(source.data),
            ).Elif(wt_edge.done,
                If(self.in_sync,
                    self.in_sync.eq(0),
                    self.source.valid.eq(1),
                    self.source.last.eq(1),
                ).Else(
                    self.source.valid.eq(0),
                    self.source.last.eq(0),
                ),
            ),
        ]


class LiteEthPHYETHERNETRX(Module):
    def __init__(self, pads, period_2):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        # Single Ended / Differential input
        rx = Signal()
        rx_i = Signal()
        if hasattr(pads, "rx"):
            rx = pads.rx
        else:
            self.specials += DifferentialInput(pads.rd_p, pads.rd_n, rx)
        self.specials += MultiReg(rx, rx_i, n=2, odomain="eth_rx")

        # Manchester decoder
        self.submodules.mrec = mrec = ClockDomainsRenamer("eth_rx")(
            ManchesterReceiver(period_2))
        self.submodules.fifo = fifo = ClockDomainsRenamer({"write": "eth_rx", "read": "sys"})(
            stream.AsyncFIFO(converter_description(1), 4))
        self.comb += [
            mrec.source.connect(fifo.sink, omit=["first"]),
            mrec.rx.eq(rx_i),
            fifo.source.ready.eq(1),
        ]

        # Bit deserialization, sync on preamble and output stream
        bitcnt = Signal(max=8)
        rx_inverted = Signal()
        data_r = Signal(8)
        self.comb += [
            If(rx_inverted,
                source.data.eq(~data_r),
            ).Else(
                source.data.eq(data_r),
            ),
            source.last_be.eq(source.last),

        ]

        self.submodules.fsm = fsm = FSM("SYNC")
        fsm.act("SYNC",
            NextValue(bitcnt, 0),
            If(fifo.source.valid,
                If(fifo.source.last,
                    NextValue(data_r, 0),
                ).Else(
                    # shift data in
                    NextValue(data_r, Cat(data_r[1:8], rx_i)),

                    # detect preamble
                    If(data_r == (eth_preamble >> 56),
                        NextValue(rx_inverted, 0),
                        NextState("RX"),
                        source.valid.eq(1),
                    ).Elif(data_r == ~(eth_preamble >> 56),
                        NextValue(rx_inverted, 1),
                        NextState("RX"),
                        source.valid.eq(1),
                    ),
                )
            ),
        )

        fsm.act("RX",
            If(fifo.source.valid,
                If(fifo.source.last,
                    source.valid.eq(1),
                    source.last.eq(1),
                    NextValue(data_r, 0),
                    NextState("SYNC"),
                ).Else(
                    # shift data in
                    NextValue(data_r, Cat(data_r[1:], rx_i)),
                    NextValue(bitcnt, bitcnt + 1),
                    If(bitcnt == 7,
                        source.valid.eq(1),
                        # NextValue(bitcnt, 0),  # implicit
                    ),
                ),
            ),
        )


class LiteEthPHYETHERNETCRG(Module, AutoCSR):
    def __init__(self, refclk_cd, with_hw_init_reset):
        self._reset = CSRStorage()

        # # #

        # RX/TX clocks
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        # This is entirely clocked internally
        assert refclk_cd

        self.comb += self.cd_eth_rx.clk.eq(ClockSignal(refclk_cd))
        self.comb += self.cd_eth_tx.clk.eq(ClockSignal(refclk_cd))

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYETHERNET(Module, AutoCSR, ModuleDoc):
    """
    Direct connection to a 10Base-T network, using only series capacitors, termination resistors and
    FPGIA IOs.
    This probably violates some parts of the IEEE802.3 standard. Use at your own risk!

    Parameters:
    - pads: either a Record or a Module for IO interface:
      - Transmit path either has `tx` and `txe`, or `td_p` and `td_n` members.
      - Receive path either has `rx` or `rd_p` and `rd_n`
    - sys_clk_freq: clock frequency at which this module runs
    - refclk_cd: ClockDomain from which the IO-facing part of the PHY runs.
      Its frequency must be 60MHz.
    """
    dw          = 8
    tx_clk_freq = 60e6
    rx_clk_freq = 60e6
    period_2    = 3

    def __init__(self, pads, sys_clk_freq, refclk_cd="eth", with_hw_init_reset=True):
        self.submodules.crg = LiteEthPHYETHERNETCRG(refclk_cd, with_hw_init_reset)
        self.submodules.tx = LiteEthPHYETHERNETTX(pads, sys_clk_freq, self.period_2)
        self.submodules.rx = LiteEthPHYETHERNETRX(pads, self.period_2)
        self.sink, self.source = self.tx.sink, self.rx.source
