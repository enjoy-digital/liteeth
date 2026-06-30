#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Shawn Hoffman <godisgovernment@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for ECP5 Lattice FPGA

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput, DDRInput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RGMII Link State ---------------------------------------------------------------------

class LiteEthRGMIILinkState:
    def __init__(self):
        self.link_up   = Signal(reset_less=True)
        self.link_10M  = Signal(reset_less=True)
        self.link_100M = Signal(reset_less=True)
        self.link_1G   = Signal(reset=1)

    def synchronize(self, to, cd):
        return [
            MultiReg(self.link_up,   to.link_up,   cd),
            MultiReg(self.link_10M,  to.link_10M,  cd),
            MultiReg(self.link_100M, to.link_100M, cd),
            MultiReg(self.link_1G,   to.link_1G,   cd),
        ]

# LiteEth PHY RGMII TX Clock -----------------------------------------------------------------------

class LiteEthRGMIITXClock(LiteXModule):
    def __init__(self, link_state=None, external_tx_clk=False):
        self.rising     = Signal(reset=1)
        self.falling    = Signal()
        self.tx_enable  = Signal(reset=1)
        self.gap_cycles = Signal(max=eth_interpacket_gap*100 + 1, reset=eth_interpacket_gap)

        # # #

        if link_state is None:
            self.comb += [
                self.rising.eq(1),
                self.falling.eq(0),
                self.tx_enable.eq(1),
                self.gap_cycles.eq(eth_interpacket_gap),
            ]
        elif external_tx_clk:
            counter        = Signal(max=50)
            counter_switch = Signal(max=25)

            self.comb += [
                If(link_state.link_1G,
                    self.rising.eq(1),
                    self.falling.eq(0),
                    self.gap_cycles.eq(eth_interpacket_gap),
                ).Else(
                    self.rising.eq(counter >= counter_switch),
                    self.falling.eq(counter > counter_switch),
                    If(link_state.link_100M,
                        self.gap_cycles.eq(eth_interpacket_gap*10),
                    ).Else(
                        self.gap_cycles.eq(eth_interpacket_gap*100),
                    )
                )
            ]
            self.sync.eth_tx += [
                self.tx_enable.eq(counter == 0),
                If(counter == 0,
                    If(link_state.link_10M,
                        counter.eq(49),
                        counter_switch.eq(24),
                    ).Elif(link_state.link_100M,
                        counter.eq(4),
                        counter_switch.eq(2),
                    ).Else(
                        counter.eq(0),
                        counter_switch.eq(0),
                    )
                ).Else(
                    counter.eq(counter - 1),
                )
            ]
        else:
            self.comb += [
                self.rising.eq(1),
                self.falling.eq(0),
                self.tx_enable.eq(1),
                If(link_state.link_1G,
                    self.gap_cycles.eq(eth_interpacket_gap),
                ).Else(
                    self.gap_cycles.eq(eth_interpacket_gap*2),
                )
            ]

# LiteEth PHY RGMII TX Datapath --------------------------------------------------------------------

class LiteEthRGMIITXDatapath(LiteXModule):
    def __init__(self, link_state=None, tx_enable=1):
        self.sink    = sink = stream.Endpoint(eth_phy_description(8))
        self.tx_ctl  = Signal(2)
        self.tx_data = Signal(8)

        # # #

        if link_state is None:
            self.comb += [
                sink.ready.eq(1),
                self.tx_ctl.eq(Cat(sink.valid, sink.valid)),
                self.tx_data.eq(sink.data),
            ]
        else:
            sdr_phase = Signal()
            sdr_data  = Signal(8)
            sdr_valid = Signal()

            self.comb += sink.ready.eq(link_state.link_1G | (tx_enable & ~sdr_phase))
            self.sync += [
                If(link_state.link_1G,
                    sdr_phase.eq(0),
                    sdr_valid.eq(0),
                    self.tx_ctl.eq(Cat(sink.valid, sink.valid)),
                    self.tx_data.eq(sink.data),
                ).Elif(tx_enable,
                    If(sdr_phase,
                        self.tx_ctl.eq(Cat(sdr_valid, sdr_valid)),
                        self.tx_data.eq(Cat(sdr_data[4:8], sdr_data[4:8])),
                        sdr_phase.eq(0),
                        sdr_valid.eq(0),
                    ).Else(
                        self.tx_ctl.eq(Cat(sink.valid, sink.valid)),
                        self.tx_data.eq(Cat(sink.data[0:4], sink.data[0:4])),
                        If(sink.valid,
                            sdr_data.eq(sink.data),
                            sdr_valid.eq(1),
                            sdr_phase.eq(1),
                        )
                    )
                )
            ]

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
    def __init__(self, pads, link_state=None, tx_enable=1):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_oddrx1f  = Signal()
        tx_data_oddrx1f = Signal(4)
        tx_ctl          = Signal(2)
        tx_data         = Signal(8)

        if link_state is None:
            self.comb += [
                sink.ready.eq(1),
                tx_ctl.eq(Cat(sink.valid, sink.valid)),
                tx_data.eq(sink.data),
            ]
        else:
            self.datapath = datapath = LiteEthRGMIITXDatapath(link_state, tx_enable)
            self.comb += [
                sink.connect(datapath.sink),
                tx_ctl.eq(datapath.tx_ctl),
                tx_data.eq(datapath.tx_data),
            ]

        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = tx_ctl[0],
                i2  = tx_ctl[1],
                o   = tx_ctl_oddrx1f,
            ),
            Instance("DELAYG",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = 0,
                i_A         = tx_ctl_oddrx1f,
                o_Z         = pads.tx_ctl,
            )
        ]
        for i in range(4):
            self.specials += [
                DDROutput(
                    clk = ClockSignal("eth_tx"),
                    i1  = tx_data[i],
                    i2  = tx_data[4+i],
                    o   = tx_data_oddrx1f[i],
                ),
                Instance("DELAYG",
                    p_DEL_MODE  = "SCLK_ALIGNED",
                    p_DEL_VALUE = 0,
                    i_A         = tx_data_oddrx1f[i],
                    o_Z         = pads.tx_data[i],
                )
            ]

# LiteEth PHY RGMII RX Datapath --------------------------------------------------------------------

class LiteEthRGMIIRXDatapath(LiteXModule):
    def __init__(self, link_state=None):
        self.rx_ctl  = rx_ctl  = Signal(2)
        self.rx_data = rx_data = Signal(8)
        self.source  = source  = stream.Endpoint(eth_phy_description(8))

        # # #

        if link_state is None:
            rx_ctl_reg   = Signal(2)
            rx_ctl_reg_d = Signal(2)
            rx_data_reg  = Signal(8)

            self.sync += [
                rx_ctl_reg.eq(rx_ctl),
                rx_ctl_reg_d.eq(rx_ctl_reg),
                rx_data_reg.eq(rx_data),
                source.valid.eq(rx_ctl_reg[0]),
                source.data.eq(rx_data_reg),
            ]
            self.comb += source.last.eq(~rx_ctl_reg[0] & rx_ctl_reg_d[0])
        else:
            sdr_phase     = Signal()
            sdr_low       = Signal(4)
            rx_ctl_rising = Signal()
            rx_ctl_d      = Signal()

            self.comb += rx_ctl_rising.eq(rx_ctl[0])
            self.sync += [
                rx_ctl_d.eq(rx_ctl_rising),
                source.valid.eq(0),
                If(link_state.link_1G,
                    sdr_phase.eq(0),
                    source.valid.eq(rx_ctl_rising),
                    source.data.eq(rx_data),
                ).Else(
                    If(rx_ctl_rising,
                        If(sdr_phase,
                            source.valid.eq(1),
                            source.data.eq(Cat(sdr_low, rx_data[0:4])),
                            sdr_phase.eq(0),
                        ).Else(
                            sdr_low.eq(rx_data[0:4]),
                            sdr_phase.eq(1),
                        )
                    ).Else(
                        sdr_phase.eq(0),
                    )
                )
            ]
            self.comb += source.last.eq(~rx_ctl_rising & rx_ctl_d)

# LiteEth PHY RGMII RX -----------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, pads, rx_delay=2e-9, with_inband_status=True, link_state=None):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        if with_inband_status:
            self.inband_status = CSRStatus(fields=[
                CSRField("link_status", size=1, description="Link status.", values=[
                    ("``0b0``", "Link down."),
                    ("``0b1``", "Link up."),
                ]),
                CSRField("clock_speed", size=2, description="Clock speed.", values=[
                    ("``0b00``", "2.5MHz   (10Mbps)."),
                    ("``0b01``", "25MHz   (100MBps)."),
                    ("``0b10``", "125MHz (1000MBps)."),
                ]),
                CSRField("duplex_status", size=1, description="Duplex status.", values=[
                    ("``0b0``", "Half-duplex."),
                    ("``0b1``", "Full-duplex."),
                ]),
            ], description="RGMII in-band status.")

        # # #

        rx_delay_taps = int(rx_delay/25e-12) # 25ps per tap
        assert rx_delay_taps < 128

        rx_ctl_delayf  = Signal()
        rx_ctl         = Signal(2)
        rx_data_delayf = Signal(4)
        rx_data        = Signal(8)

        self.specials += [
            Instance("DELAYG",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = rx_delay_taps,
                i_A         = pads.rx_ctl,
                o_Z         = rx_ctl_delayf,
            ),
            DDRInput(
                clk = ClockSignal("eth_rx"),
                i   = rx_ctl_delayf,
                o1  = rx_ctl[0],
                o2  = rx_ctl[1],
            )
        ]
        for i in range(4):
            self.specials += [
                Instance("DELAYG",
                    p_DEL_MODE  = "SCLK_ALIGNED",
                    p_DEL_VALUE = rx_delay_taps,
                    i_A         = pads.rx_data[i],
                    o_Z         = rx_data_delayf[i]),
                DDRInput(
                    clk = ClockSignal("eth_rx"),
                    i   = rx_data_delayf[i],
                    o1  = rx_data[i],
                    o2  = rx_data[i+4],
                )
            ]

        self.datapath = datapath = LiteEthRGMIIRXDatapath(link_state)
        self.comb += [
            datapath.rx_ctl.eq(rx_ctl),
            datapath.rx_data.eq(rx_data),
        ]
        self.comb += datapath.source.connect(source)

        if with_inband_status:
            inband_status = [
                self.inband_status.fields.link_status.eq(  rx_data[0]),
                self.inband_status.fields.clock_speed.eq(  rx_data[1:3]),
                self.inband_status.fields.duplex_status.eq(rx_data[3]),
            ]
            if link_state is not None:
                inband_status += [
                    link_state.link_up.eq(rx_data[0]),
                    link_state.link_10M.eq(rx_data[1:3] == 0b00),
                    link_state.link_100M.eq(rx_data[1:3] == 0b01),
                    link_state.link_1G.eq(rx_data[1:3] == 0b10),
                ]
            self.sync += [
                If(rx_ctl == 0b00,
                    *inband_status
                )
            ]

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9, tx_clk=None,
        link_state = None,
    ):
        self._reset = CSRStorage(description="PHY reset.")
        self.tx_enable     = Signal(reset=1)
        self.tx_gap_cycles = Signal(max=eth_interpacket_gap*100 + 1, reset=eth_interpacket_gap)

        # # #

        # RX Clock
        self.cd_eth_rx = ClockDomain()
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX Clock
        self.cd_eth_tx = ClockDomain()
        if isinstance(tx_clk, Signal):
            self.comb += self.cd_eth_tx.clk.eq(tx_clk)
        else:
            self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)

        tx_delay_taps = int(tx_delay/25e-12) # 25ps per tap
        assert tx_delay_taps < 128

        self.tx_clock = tx_clock = LiteEthRGMIITXClock(
            link_state      = link_state,
            external_tx_clk = isinstance(tx_clk, Signal),
        )
        self.comb += [
            self.tx_enable.eq(tx_clock.tx_enable),
            self.tx_gap_cycles.eq(tx_clock.gap_cycles),
        ]

        eth_tx_clk_o = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = tx_clock.rising,
                i2  = tx_clock.falling,
                o   = eth_tx_clk_o,
            ),
            Instance("DELAYG",
                p_DEL_MODE  = "SCLK_ALIGNED",
                p_DEL_VALUE = tx_delay_taps,
                i_A         = eth_tx_clk_o,
                o_Z         = clock_pads.tx,
            )
        ]

        # Reset
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYRGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, clock_pads, pads, with_hw_init_reset=True,
        tx_delay           = 2e-9,
        rx_delay           = 2e-9,
        with_inband_status = True,
        tx_clk             = None,
        with_dynamic_link  = False,
        ):
        assert not (with_dynamic_link and not with_inband_status)

        link_state_rx = None
        link_state_tx = None
        if with_dynamic_link:
            self.link_state_rx = link_state_rx = LiteEthRGMIILinkState()
            self.link_state_tx = link_state_tx = LiteEthRGMIILinkState()
            self.specials += link_state_rx.synchronize(link_state_tx, "eth_tx")

        self.crg = LiteEthPHYRGMIICRG(
            clock_pads,
            pads,
            with_hw_init_reset,
            tx_delay,
            tx_clk,
            link_state = link_state_tx,
        )
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(
            pads,
            link_state = link_state_tx,
            tx_enable  = self.crg.tx_enable,
        ))
        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(
            pads,
            rx_delay,
            with_inband_status,
            link_state = link_state_rx,
        ))
        self.sink, self.source = self.tx.sink, self.rx.source
        if with_dynamic_link:
            self.tx_gap_cycles = self.crg.tx_gap_cycles

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
