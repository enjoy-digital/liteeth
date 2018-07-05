from liteeth.common import *
from liteeth.phy.gmii import LiteEthPHYGMIICRG
from liteeth.phy.mii import LiteEthPHYMIITX, LiteEthPHYMIIRX
from liteeth.phy.gmii import LiteEthPHYGMIITX, LiteEthPHYGMIIRX

from migen.genlib.io import DDROutput
from migen.genlib.cdc import PulseSynchronizer

from litex.soc.interconnect.stream import Multiplexer, Demultiplexer

from liteeth.phy.common import LiteEthPHYMDIO


modes = {
    "GMII": 0,
    "MII": 1
}

tx_pads_layout = [("tx_er", 1), ("tx_en", 1), ("tx_data", 8)]
rx_pads_layout = [("rx_er", 1), ("rx_dv", 1), ("rx_data", 8)]


class LiteEthPHYGMIIMIITX(Module):
    def __init__(self, pads, mode):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        gmii_tx_pads = Record(tx_pads_layout)
        gmii_tx = LiteEthPHYGMIITX(gmii_tx_pads)
        self.submodules += gmii_tx

        mii_tx_pads = Record(tx_pads_layout)
        mii_tx = LiteEthPHYMIITX(mii_tx_pads)
        self.submodules += mii_tx

        demux = Demultiplexer(eth_phy_description(8), 2)
        self.submodules += demux
        self.comb += [
            demux.sel.eq(mode == modes["MII"]),
            sink.connect(demux.sink),
            demux.source0.connect(gmii_tx.sink),
            demux.source1.connect(mii_tx.sink),
        ]


        if hasattr(pads, "tx_er"):
            pads.tx_er.reset_less = True
            self.comb += pads.tx_er.eq(0)
        pads.tx_en.reset_less = True
        pads.tx_data.reset_less = True
        self.sync += [
            If(mode == modes["MII"],
                pads.tx_en.eq(mii_tx_pads.tx_en),
                pads.tx_data.eq(mii_tx_pads.tx_data),
            ).Else(
                pads.tx_en.eq(gmii_tx_pads.tx_en),
                pads.tx_data.eq(gmii_tx_pads.tx_data),
            )
        ]


class LiteEthPHYGMIIMIIRX(Module):
    def __init__(self, pads, mode):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        pads_d = Record(rx_pads_layout)
        pads_d.rx_dv.reset_less = True
        pads_d.rx_data.reset_less = True
        self.sync += [
            pads_d.rx_dv.eq(pads.rx_dv),
            pads_d.rx_data.eq(pads.rx_data)
        ]

        gmii_rx = LiteEthPHYGMIIRX(pads_d)
        self.submodules += gmii_rx

        mii_rx = LiteEthPHYMIIRX(pads_d)
        self.submodules += mii_rx

        mux = Multiplexer(eth_phy_description(8), 2)
        self.submodules += mux
        self.comb += [
            mux.sel.eq(mode == modes["MII"]),
            gmii_rx.source.connect(mux.sink0),
            mii_rx.source.connect(mux.sink1),
            mux.source.connect(source)
        ]


class LiteEthGMIIMIIModeDetection(Module, AutoCSR):
    def __init__(self, clk_freq):
        self.mode = Signal()
        self._mode = CSRStatus()

        # # #

        mode = Signal()
        update_mode = Signal()
        self.sync += \
            If(update_mode,
                self.mode.eq(mode)
            )
        self.comb += self._mode.status.eq(self.mode)

        # Principle:
        #  sys_clk >= 125MHz
        #  eth_rx <= 125Mhz
        # We generate ticks every 1024 clock cycles in eth_rx domain
        # and measure ticks period in sys_clk domain.

        # Generate a tick every 1024 clock cycles (eth_rx clock domain)
        eth_tick = Signal()
        eth_counter = Signal(10, reset_less=True)
        self.sync.eth_rx += eth_counter.eq(eth_counter + 1)
        self.comb += eth_tick.eq(eth_counter == 0)

        # Synchronize tick (sys clock domain)
        sys_tick = Signal()
        eth_ps = PulseSynchronizer("eth_rx", "sys")
        self.comb += [
            eth_ps.i.eq(eth_tick),
            sys_tick.eq(eth_ps.o)
        ]
        self.submodules += eth_ps

        # sys_clk domain counter
        sys_counter = Signal(24, reset_less=True)
        sys_counter_reset = Signal()
        sys_counter_ce = Signal()
        self.sync += [
            If(sys_counter_reset,
               sys_counter.eq(0)
            ).Elif(sys_counter_ce,
                sys_counter.eq(sys_counter + 1)
            )
        ]

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        fsm.act("IDLE",
            sys_counter_reset.eq(1),
            If(sys_tick,
                NextState("COUNT")
            )
        )
        fsm.act("COUNT",
            sys_counter_ce.eq(1),
            If(sys_tick,
                NextState("DETECTION")
            )
        )
        fsm.act("DETECTION",
            update_mode.eq(1),
            # if freq < 125MHz-5% use MII mode
            If(sys_counter > int((clk_freq/125000000)*1024*1.05),
                mode.eq(1)
            # if freq >= 125MHz-5% use GMII mode
            ).Else(
                mode.eq(0)
            ),
            NextState("IDLE")
        )


class LiteEthPHYGMIIMII(Module, AutoCSR):
    def __init__(self, clock_pads, pads, clk_freq, with_hw_init_reset=True):
        self.dw = 8
        # Note: we can use GMII CRG since it also handles tx clock pad used for MII
        self.submodules.mode_detection = LiteEthGMIIMIIModeDetection(clk_freq)
        mode = self.mode_detection.mode
        self.submodules.crg = LiteEthPHYGMIICRG(clock_pads, pads, with_hw_init_reset, mode == modes["MII"])
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYGMIIMIITX(pads, mode))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYGMIIMIIRX(pads, mode))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)