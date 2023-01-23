#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.io import DDROutput

from liteeth.common import *
from liteeth.phy.common import *


def converter_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)


class LiteEthPHYRMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        converter = stream.StrideConverter(converter_description(8),
                                           converter_description(2))
        self.submodules += converter
        self.comb += [
            converter.sink.valid.eq(sink.valid),
            converter.sink.data.eq(sink.data),
            sink.ready.eq(converter.sink.ready),
            converter.source.ready.eq(1)
        ]
        pads.tx_en.reset_less = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(converter.source.valid),
            pads.tx_data.eq(converter.source.data)
        ]


class LiteEthPHYRMIIRX(Module):
    def __init__(self, pads, hw_init_mode_cfg):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        converter = stream.StrideConverter(converter_description(2),
                                           converter_description(8))
        converter = ResetInserter()(converter)
        self.submodules += converter

        converter_sink_valid = Signal()
        converter_sink_data = Signal(2)

        self.specials += [
            MultiReg(converter_sink_valid, converter.sink.valid, n=2),
            MultiReg(converter_sink_data, converter.sink.data, n=2)
        ]

        crs_dv = Signal()
        crs_dv_d = Signal()
        rx_data = Signal(2)

        if hw_init_mode_cfg:
            self.sync += [
                crs_dv.eq(pads.crs_dv_i),
                crs_dv_d.eq(crs_dv),
                rx_data.eq(pads.rx_data_i)
            ]
        else:
            self.sync += [
                crs_dv.eq(pads.crs_dv),
                crs_dv_d.eq(crs_dv),
                rx_data.eq(pads.rx_data)
            ]

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(crs_dv & (rx_data != 0b00),
                converter_sink_valid.eq(1),
                converter_sink_data.eq(rx_data),
                NextState("RECEIVE")
            ).Else(
               converter.reset.eq(1)
            )
        )
        fsm.act("RECEIVE",
            converter_sink_valid.eq(1),
            converter_sink_data.eq(rx_data),
            # end of frame when 2 consecutives 0 on crs_dv
            If(~(crs_dv | crs_dv_d),
              converter.sink.last.eq(1),
              NextState("IDLE")
            )
        )
        self.comb += converter.source.connect(source)


class LiteEthPHYRMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, refclk_cd, with_hw_init_reset, hw_init_mode_cfg):
        self._reset = CSRStorage()

        # # #

        # RX/TX clocks

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        # When no refclk_cd, use clock_pads.ref_clk as RMII reference clock.
        if refclk_cd is None:
            self.comb += self.cd_eth_rx.clk.eq(clock_pads.ref_clk)
            self.comb += self.cd_eth_tx.clk.eq(clock_pads.ref_clk)

        # Else use refclk_cd as RMII reference clock (provided by user design).
        else:
            self.comb += self.cd_eth_rx.clk.eq(ClockSignal(refclk_cd))
            self.comb += self.cd_eth_tx.clk.eq(ClockSignal(refclk_cd))
            # Drive clock_pads if provided.
            if clock_pads is not None:
                self.specials += DDROutput(0, 1, clock_pads.ref_clk, ClockSignal("eth_tx"))

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)

        if hw_init_mode_cfg:
            self.specials += [
                Tristate(pads.rx_data, pads.rx_data_o, reset, pads.rx_data_i),
                Tristate(pads.crs_dv, pads.crs_dv_o, reset, pads.crs_dv_i),
            ]
            self.comb += [
                If(self.reset,
                    pads.rx_data_o[0].eq(hw_init_mode_cfg[0]),
                    pads.rx_data_o[1].eq(hw_init_mode_cfg[1]),
                    pads.crs_dv_o.eq(hw_init_mode_cfg[2]),
                )
            ]

        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRMII(Module, AutoCSR):
    dw          = 8
    tx_clk_freq = 50e6
    rx_clk_freq = 50e6
    def __init__(self, clock_pads, pads, refclk_cd="eth", with_hw_init_reset=True, hw_init_mode_cfg=False):

        if hw_init_mode_cfg:
            rx_data_o = Signal(2)
            rx_data_i = Signal(2)
            crs_dv_o = Signal()
            crs_dv_i = Signal()
            pads.rx_data_o = rx_data_o
            pads.rx_data_i = rx_data_i
            pads.crs_dv_o = crs_dv_o
            pads.crs_dv_i = crs_dv_i

        self.submodules.crg = LiteEthPHYRMIICRG(clock_pads, pads, refclk_cd, with_hw_init_reset, hw_init_mode_cfg)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYRMIITX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYRMIIRX(pads, hw_init_mode_cfg))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
