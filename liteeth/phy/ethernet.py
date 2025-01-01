#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2021 Charles-Henri Mousset <ch.mousset@gmail.com>
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
        tx = Signal(reset_less=True)
        txe = Signal(reset_less=True)
        self.comb += tx_bit_strb.eq(tx_cnt == 0b11)
        self.sync += [tx_cnt.eq(tx_cnt+1)]

        # Output logic
        if hasattr(pads, "tx") and hasattr(pads, "tx_en"): # RS485 half duplex mode
            self.comb += [pads.tx.eq(tx), pads.txe.eq(txe)]
        else: # A true differential output buffer is not necessary at 20Mbps(manchester)
            self.comb += [pads.td_p.eq(tx & txe), pads.td_n.eq(~tx & txe)]


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
            tx.eq(tx_bit ^ tx_cnt[1]),
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
        rx = Signal()
        if not hasattr(pads, "rx"):
            self.specials += DifferentialInput(pads.rd_p, pads.rd_n, rx)
        else:
            self.comb += rx.eq(pads.rx)

        # Manchester input
        mc_in_data = Signal(3)
        mc_cnt = Signal(2)
        bit_valid = Signal()
        bit_value = Signal()
        self.comb += [
            bit_valid.eq((mc_in_data[2] ^ mc_in_data[1]) & (mc_cnt == 0b00)),
            bit_value.eq(mc_in_data[2]),
        ]
        self.sync += [
            mc_in_data.eq(Cat(rx, mc_in_data[0:1])),
            If(bit_valid,
                mc_cnt.eq(3),
            ).Elif(mc_cnt,
                mc_cnt.eq(mc_cnt-1),
            ),
        ]

        # Receive timeout / NLP and noise filter
        timeout_cnt = Signal(3)
        timeout = Signal()
        self.comb += [
            timeout.eq(timeout_cnt == 0),
        ]
        self.sync += [
            If(bit_valid,
                timeout_cnt.eq(0b111),
            ).Elif(timeout_cnt,
                timeout_cnt.eq(timeout_cnt - 1),
            )
        ]

        # bit to byte logic
        bit_cnt = Signal(3)
        byte = Signal(8)

        self.sync += [
            If(timeout,
                bit_cnt.eq(0),
            ).Elif(bit_valid,
                bit_cnt.eq(bit_cnt+1),
                byte.eq(Cat(byte[1:], bit_value)),
            ),
        ]
        self.comb += [
            self.source.valid.eq((bit_cnt == 7) & bit_valid),
            self.source.data.eq(byte),
        ]


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
