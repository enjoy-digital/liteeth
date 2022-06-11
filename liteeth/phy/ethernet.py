#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2021-2022 Charles-Henri Mousset <ch.mousset@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.io import DDROutput

from liteeth.common import *
from liteeth.phy.common import *

from litex.build.io import DifferentialInput
from litex.soc.integration.doc import AutoDoc, ModuleDoc


def converter_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)


class LiteEthPHYETHERNETTX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))
        self.submodules.fsm = fsm = FSM("IDLE")

        # # #
        # deserializing
        converter = stream.StrideConverter(converter_description(8),
                                           converter_description(1))
        self.submodules += converter
        self.comb += [
            converter.sink.valid.eq(sink.valid),
            converter.sink.data.eq(sink.data),
            sink.ready.eq(converter.sink.ready),
            converter.source.ready.eq(fsm.ongoing("TX"))
        ]

        # Manchester encoding
        tx_bit = Signal()
        tx_cnt = Signal(2)
        tx_bit_strb = Signal()
        self.tx = tx = Signal(reset_less=True)
        txe = Signal(reset_less=True)
        self.comb += tx_bit_strb.eq(tx_cnt == 0b11)
        self.sync += [tx_cnt.eq(tx_cnt+1)]

        # Output logic
        if hasattr(pads, "tx") and hasattr(pads, "tx_en"): # RS485 half duplex mode
            self.comb += [pads.tx.eq(tx), pads.txe.eq(txe)]
        else: # A true differential output buffer is not necessary at 20Mbps(manchester)
            self.specials += Tristate(pads.td_p, tx, txe)
            self.specials += Tristate(pads.td_n, ~tx, txe)

        # Normal Link Pulse generation timer
        NLP_PERIOD = int(40e6/1000*16)
        nlp_timeout = Signal()
        nlp_counter = Signal(max=NLP_PERIOD+1)
        self.comb += [
            nlp_timeout.eq(nlp_counter == NLP_PERIOD-1),
        ]
        self.sync += [
            If(nlp_timeout,
                nlp_counter.eq(0),
            ).Else(
                nlp_counter.eq(nlp_counter+1),
            ),
        ]

        # Main FSM
        fsm.act("IDLE",
            # send the Normal Link Pulses
            If(nlp_timeout & (tx_bit_strb),
                NextState("NLP1"),
            ),
            converter.sink.ready.eq(tx_bit_strb),
            If(converter.sink.valid & converter.sink.ready,
                NextState("TX"),
                NextValue(tx_bit, converter.sink.data),
            ),
        )
        fsm.act("NLP1",
            # we emit a '1' for 1 bit time
            tx.eq(1),
            txe.eq(1),
            If(tx_bit_strb,
                NextState("NLP2"),
            )
        )
        fsm.act("NLP2",
            # we emit a '0' for 1 bit time
            tx.eq(0),
            txe.eq(1),
            If(tx_bit_strb,
                NextState("IDLE"),
            )
        )
        fsm.act("TX",
            tx.eq((~tx_bit) ^ tx_cnt[1]),
            txe.eq(converter.sink.valid), # should stay at 1
            If(tx_bit_strb,
                converter.sink.ready.eq(1),
                NextValue(tx_bit, converter.sink.data),
            ),
            If(sink.last_be,
                NextState("IDLE"),
            )
        )


class LiteEthPHYETHERNETRX(Module):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        # Single Ended / Differential input
        self.rx = rx = Signal()
        self.rx_i = rx_i = Signal()
        if hasattr(pads, "rx"):
            self.comb += rx.eq(pads.rx)
        else:
            self.specials += DifferentialInput(pads.rd_p, pads.rd_n, rx)
        self.specials += MultiReg(rx, rx_i)

        # Timing edge logic
        edge = Signal()
        timeout = Signal()
        bit_period = Signal()
        rx_i_old = Signal()
        timeout_cnt_max = 5
        bit_period_cnt_max = 2
        timeout_cnt = Signal(max=timeout_cnt_max + 1)
        bit_period_cnt = Signal(max=bit_period_cnt_max + 1)

        self.sync += [
            rx_i_old.eq(rx_i),
            If(edge,
                bit_period_cnt.eq(0),
                timeout_cnt.eq(0),
            ).Else(
                If(~timeout,
                    timeout_cnt.eq(timeout_cnt + 1),
                ),
                If(~bit_period,
                    bit_period_cnt.eq(bit_period_cnt + 1),
                ),
            ),
        ]
        self.comb += [
            edge.eq(rx_i ^ rx_i_old),
            bit_period.eq(bit_period_cnt == bit_period_cnt_max),
            timeout.eq(timeout_cnt == timeout_cnt_max),
        ]

        # noise detection
        noise = Signal()
        self.comb += noise.eq(edge & (bit_period_cnt == 0))

        # Byte logic
        bitcnt = Signal(max=8 + 1)
        rx_inverted = Signal()
        half_bit = Signal()
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
        fsm.act("IDLE",
            # Wait for activity
            If(edge,
                NextState("SYNC"),
            ),
        )

        fsm.act("SYNC",
            NextValue(bitcnt, 1),
            # Wait for the preamble to sync on byte-boundaries
            If(edge,
                NextValue(half_bit, ~half_bit),
                If(~half_bit | bit_period,
                    # shift data in
                    NextValue(data_r, Cat(data_r[1:], rx_i)),
                    NextValue(half_bit, 1),

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
                ),
            ),
            If(timeout | noise,
                NextValue(data_r, 0),
            ),
        )

        fsm.act("RX",
            If(edge,
                NextValue(half_bit, ~half_bit),
                If(~half_bit | bit_period,
                    # shift data in
                    NextValue(data_r, Cat(data_r[1:], rx_i)),
                    NextValue(bitcnt, bitcnt + 1),
                    NextValue(half_bit, 1),
                    If(bitcnt == 8,
                        source.valid.eq(1),
                        NextValue(bitcnt, 1),
                    ),
                ),
            ),
            # Wait for a timeout to go into idle
            If(timeout,
                source.valid.eq(1),
                source.last.eq(1),
                NextState("SYNC"),
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
    Direct connection to a 10Base-T network, using only series capacitors with FPGIO IOs.
    This probably violates some parts of the IEEE802.3 standard. Use at your own risk!
    """
    dw          = 8
    tx_clk_freq = 40e6
    rx_clk_freq = 40e6
    def __init__(self, pads, refclk_cd="eth", with_hw_init_reset=True):
        self.submodules.crg = LiteEthPHYETHERNETCRG(refclk_cd, with_hw_init_reset)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYETHERNETTX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYETHERNETRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source
