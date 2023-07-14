#
# This file is part of LiteEth.
#
# Copyright (c) 2019-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Shawn Hoffman <godisgovernment@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for ECP5 Lattice FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex.build.io import DDROutput, DDRInput

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RGMII LINK Status --------------------------------------------------------------------

class LiteEthOneHotLinkState(LiteXModule):
    def __init__(self):
        # Encode link status as one hot so we can easily use a DFF synchronizer
        self.link_up = Signal(reset_less=True)
        self.link_10M = Signal(reset_less=True)
        self.link_100M = Signal(reset_less=True)
        self.link_1G = Signal(reset_less=True)

    def synchronize(self, to, cd):
        return [
            MultiReg(
                self.link_up,
                to.link_up,
                cd
            ),
            MultiReg(
                self.link_1G,
                to.link_1G,
                cd
            ),
            MultiReg(
                self.link_100M,
                to.link_100M,
                cd
            ),
            MultiReg(
                self.link_10M,
                to.link_10M,
                cd
            )
        ]

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
    def __init__(self, pads, linkstate=None, clk_en=None):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        tx_ctl_oddrx1f  = Signal()
        tx_data_oddrx1f = Signal(4)

        valid = Signal()
        data = Signal(8)
        ready = Signal()

        if linkstate is not None:
            ready_next = Signal()
            nibble_select = Signal()

            # If link speed is not 1G we need to take care
            # that the DDR lines only transfer 4-bit of data at a time
            # This means we need to provide back pressure on non-1G link speeds
            self.sync += If(clk_en,
                ready_next.eq(~ready_next & sink.valid)
            )

            self.sync += If(linkstate.link_1G,
                data.eq(sink.data)
            ).Elif(clk_en & nibble_select,
                data.eq(Cat(sink.data[4:8], sink.data[4:8]))
            ).Elif(clk_en,
                data.eq(Cat(sink.data[0:4], sink.data[0:4]))
            )

            self.sync += If(clk_en,
                valid.eq(sink.valid)
            )

            self.sync += If(linkstate.link_1G,
                ready.eq(1),
            ).Elif(clk_en,
                ready.eq(ready_next)
            ).Else(
                ready.eq(0)
            )

            self.sync += If(linkstate.link_1G,
                nibble_select.eq(0)
            ).Elif(clk_en & sink.valid,
                nibble_select.eq(~nibble_select)
            ).Else(
                nibble_select.eq(nibble_select)
            )

        else:
            self.comb += [
                ready.eq(1),
                valid.eq(sink.valid),
                data.eq(sink.data)
            ]

        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = valid,
                i2  = valid,
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
                    i1  = data[i],
                    i2  = data[4+i],
                    o   = tx_data_oddrx1f[i],
                ),
                Instance("DELAYG",
                    p_DEL_MODE  = "SCLK_ALIGNED",
                    p_DEL_VALUE = 0,
                    i_A         = tx_data_oddrx1f[i],
                    o_Z         = pads.tx_data[i],
                )
            ]
        self.comb += sink.ready.eq(ready)

# LiteEth PHY RGMII RX -----------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, pads, rx_delay=2e-9, with_inband_status=True, linkstate=None):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        if with_inband_status:
            self.inband_status = CSRStatus(fields=[
                CSRField("link_status", size=1, values=[
                    ("``0b0``", "Link down."),
                    ("``0b1``", "Link up."),
                ]),
                CSRField("clock_speed", size=1, values=[
                    ("``0b00``", "2.5MHz   (10Mbps)."),
                    ("``0b01``", "25MHz   (100MBps)."),
                    ("``0b10``", "125MHz (1000MBps)."),
                ]),
                CSRField("duplex_status", size=1, values=[
                    ("``0b0``", "Half-duplex."),
                    ("``0b1``", "Full-duplex."),
                ]),
            ])

        # # #

        rx_delay_taps = int(rx_delay/25e-12) # 25ps per tap
        assert rx_delay_taps < 128

        rx_ctl_delayf  = Signal()
        rx_ctl         = Signal(2)
        rx_ctl_reg     = Signal(2)
        rx_data_delayf = Signal(4)
        rx_data        = Signal(8)
        rx_data_reg    = Signal(8)

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
        self.sync += rx_ctl_reg.eq(rx_ctl)
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

        # 1G has DDR, 100m and 10m are SDR on Rising edge only
        valid_reg = Signal()
        valid_last0 = Signal()
        valid_last1 = Signal()

        if linkstate is not None:
            valid_sdr = Signal()

            self.sync += If(linkstate.link_1G,
                rx_data_reg.eq(rx_data),
            ).Else(
                rx_data_reg.eq(Cat(rx_data_reg[4:8], rx_data[0:4])),
            )

            self.sync += If(linkstate.link_1G,
                valid_sdr.eq(0),
                valid_reg.eq(rx_ctl[0])
            ).Elif(rx_ctl[0],
                valid_sdr.eq(~valid_sdr),
                valid_reg.eq(valid_sdr)
            ).Else(
                valid_reg.eq(valid_sdr)
            )

            self.sync += valid_last0.eq(rx_ctl[0])
        else:
            self.sync += [
                rx_data_reg.eq(rx_data),
                valid_reg.eq(rx_ctl[0])
            ]

            self.comb += valid_last0.eq(valid_reg)

        self.sync += valid_last1.eq(valid_last0)

        last = Signal()
        self.comb += last.eq(~valid_last0 & valid_last1)
        self.sync += [
            source.valid.eq(valid_reg),
            source.data.eq(rx_data_reg)
        ]
        self.comb += source.last.eq(last)

        if with_inband_status:
            self.sync += [
                If(rx_ctl == 0b00,
                    self.inband_status.fields.link_status.eq(  rx_data[0]),
                    self.inband_status.fields.clock_speed.eq(  rx_data[1:3]),
                    self.inband_status.fields.duplex_status.eq(rx_data[3]),
                    linkstate.link_up.eq(rx_data[0]),
                    linkstate.link_10M.eq(rx_data[1:3] == 0b00),
                    linkstate.link_100M.eq(rx_data[1:3] == 0b01),
                    linkstate.link_1G.eq(rx_data[1:3] == 0b10),
                )
            ]

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, clock_pads, pads, with_hw_init_reset, tx_delay=2e-9, tx_clk=None, linkstate_rx=None, linkstate_tx=None):
        self._reset = CSRStorage()

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

        self.tx_clk_en = Signal(reset_less=True)
        rising_edge = Signal()
        falling_edge = Signal()

        # Without loop-clocking we need to divide the 125Mhz clock
        # into either 25Mhz or 2.5Mhz. Some Phys require the clock to
        # be glitchless
        if linkstate_rx is not None and tx_clk is not None:
            with_switch = Signal()
            counter_switch = Signal(5)
            counter = Signal(6)
            counter_rst = Signal(1)

            self.comb += counter_rst.eq(counter == 0)

            # clock divider for 100M and 10M
            self.sync.eth_tx += If(counter_rst,
                If(linkstate_tx.link_10M,
                    with_switch.eq(0),
                    counter_switch.eq(24),
                    counter.eq(49)
                ).Elif(linkstate_tx.link_100M,
                    with_switch.eq(1),
                    counter_switch.eq(2),
                    counter.eq(4)
                ).Else(
                    with_switch.eq(1),
                    counter_switch.eq(0),
                    counter.eq(0)
                )
            ).Else(
                counter.eq(counter - 1)
            )

            at_switch = Signal()
            self.comb += at_switch.eq(counter == counter_switch)

            self.sync.eth_tx += If(with_switch & at_switch,
                rising_edge.eq(1),
                falling_edge.eq(0),
            ).Elif(counter > counter_switch,
                rising_edge.eq(1),
                falling_edge.eq(1),
            ).Else(
                rising_edge.eq(0),
                falling_edge.eq(0)
            )

            self.sync.eth_tx += self.tx_clk_en.eq(counter == 0)

        # When loop clocking we don't need to divide the tx clock since it will
        # allready be at the correct speed.
        elif linkstate_rx is not None:
            self.comb += [
                self.tx_clk_en.eq(1),
                rising_edge.eq(1),
                falling_edge.eq(0)
            ]
        else:
            self.comb += [
                rising_edge.eq(1),
                falling_edge.eq(0)
            ]

        eth_tx_clk_o = Signal()
        self.specials += [
            DDROutput(
                clk = ClockSignal("eth_tx"),
                i1  = rising_edge,
                i2  = falling_edge,
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
        linkstate          = None
        ):

        # We cannot have two sources of linkstate
        assert not (linkstate is not None and with_inband_status)

        linkstate_rx = None
        linkstate_tx = None
        if linkstate or with_inband_status:
            linkstate_rx = LiteEthOneHotLinkState()

        # If we have a source of link state from sys we need to sync it to rx clk
        if linkstate:
            self.specials += linkstate.synchronize(linkstate_rx, "eth_rx")


        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(
            pads, rx_delay, with_inband_status,
            linkstate=linkstate_rx
        ))

        if linkstate_rx is not None:
            linkstate_tx = linkstate_rx
            # If we aren't loop clocking the tx is clock is different from the rx
            # clock and we need to synchronize
            if tx_clk is not None:
                linkstate_src = linkstate if linkstate is not None else linkstate_rx
                linkstate_tx = LiteEthOneHotLinkState()
                self.specials += linkstate_src.synchronize(linkstate_tx, "eth_tx")

            if linkstate is None:
                linkstate = LiteEthOneHotLinkState()
                self.specials += linkstate_rx.synchronize(linkstate, "sys")

        self.crg = LiteEthPHYRGMIICRG(
            clock_pads, pads, with_hw_init_reset, tx_delay, tx_clk,
            linkstate_rx, linkstate_tx
        )

        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(
            pads, linkstate=linkstate_rx, clk_en = self.crg.tx_clk_en
        ))
        self.sink, self.source = self.tx.sink, self.rx.source

        # Create a custom gap length to paper over transmit clock and Phy
        # internal clock periods beeing different
        if linkstate is not None and tx_clk is not None:
            self.gap = Signal(max=eth_interpacket_gap*100, reset_less = True)

            self.sync += If(linkstate.link_1G,
                self.gap.eq(eth_interpacket_gap)
            ).Elif(linkstate.link_100M,
                self.gap.eq(eth_interpacket_gap*10)
            ).Else(
                self.gap.eq(eth_interpacket_gap*100)
            )
        elif linkstate is not None:
            self.gap = Signal(max=eth_interpacket_gap*2, reset_less = True)

            self.sync += If(linkstate.link_1G,
                self.gap.eq(eth_interpacket_gap)
            ).Else(
                self.gap.eq(eth_interpacket_gap*2)
            )

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
