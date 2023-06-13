#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Franck Jullien <franck.jullien@collshade.fr>
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# RGMII PHY for Trion Efinix FPGA

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.generic_platform import *
from litex.soc.cores.clock import *

from liteeth.common import *
from liteeth.phy.common import *

# LiteEth PHY RGMII TX -----------------------------------------------------------------------------

class LiteEthPHYRGMIITX(LiteXModule):
    def __init__(self, platform, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        # TX Data IOs.
        # ------------
        tx_data_h = []
        tx_data_l = []
        for n in range(4):
                name    = platform.get_pin_name(pads.tx_data[n])
                pad     = platform.get_pin_location(pads.tx_data[n])
                io_prop = platform.get_pin_properties(pads.tx_data[n])
                name    = f"auto_{name}"

                tx_data_h.append(platform.add_iface_io(name + "_HI"))
                tx_data_l.append(platform.add_iface_io(name + "_LO"))

                block = {
                    "type"              : "GPIO",
                    "mode"              : "OUTPUT",
                    "name"              : name,
                    "location"          : pad,
                    "properties"        : io_prop,
                    "size"              : 1,
                    "out_reg"           : "DDIO_RESYNC",
                    "out_clk_pin"       : "auto_eth_tx_clk",
                    "is_inclk_inverted" : False,
                    "drive_strength"    : 4 # FIXME: Get it from constraints.
                }
                platform.toolchain.ifacewriter.blocks.append(block)

        # TX Ctl IOs.
        # -----------
        self.sync.eth_tx += pads.tx_ctl.eq(sink.valid)

        # Logic.
        # ------
        self.comb += sink.ready.eq(1)
        for n in range(4):
            self.sync += [
                tx_data_h[n].eq(sink.data[n + 0]),
                tx_data_l[n].eq(sink.data[n + 4])
            ]

# LiteEth PHY RGMII RX -----------------------------------------------------------------------------

class LiteEthPHYRGMIIRX(LiteXModule):
    def __init__(self, platform, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        # RX Data IOs.
        # ------------
        rx_data_h = []
        rx_data_l = []
        for n in range(4):
            name    = platform.get_pin_name(pads.rx_data[n])
            pad     = platform.get_pin_location(pads.rx_data[n])
            io_prop = platform.get_pin_properties(pads.rx_data[n])
            name    = f"auto_{name}"

            rx_data_h.append(platform.add_iface_io(name + "_HI"))
            rx_data_l.append(platform.add_iface_io(name + "_LO"))

            block = {
                "type"              : "GPIO",
                "mode"              : "INPUT",
                "name"              : name,
                "location"          : pad,
                "properties"        : io_prop,
                "size"              : 1,
                "in_reg"            : "DDIO_RESYNC",
                "in_clk_pin"        : "auto_eth_rx_clk",
                "is_inclk_inverted" : False
            }
            platform.toolchain.ifacewriter.blocks.append(block)

        # RX Ctl IOs.
        # -----------
        rx_ctl_d = Signal()
        self.sync += rx_ctl_d.eq(pads.rx_ctl)

        # Logic.
        # ------
        last    = Signal()
        rx_data = Signal(8)
        for n in range(4):
            self.comb += rx_data[n + 0].eq(rx_data_l[n])
            self.comb += rx_data[n + 4].eq(rx_data_h[n])
        self.comb += last.eq(~pads.rx_ctl & rx_ctl_d)
        self.sync += [
            source.valid.eq(rx_ctl_d),
            source.data.eq(rx_data)
        ]
        self.comb += source.last.eq(last)

# LiteEth PHY RGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYRGMIICRG(LiteXModule):
    def __init__(self, platform, clock_pads, with_hw_init_reset, hw_reset_cycles=256):
        self._reset = CSRStorage()

        # # #

        # Clk Domains.
        # ------------
        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()

        # RX Clk.
        # -------
        eth_rx_clk = platform.add_iface_io("auto_eth_rx_clk")
        block = {
            "type"       : "GPIO",
            "size"       : 1,
            "location"   : platform.get_pin_location(clock_pads.rx)[0],
            "properties" : platform.get_pin_properties(clock_pads.rx),
            "name"       : platform.get_pin_name(eth_rx_clk),
            "mode"       : "INPUT_CLK"
        }
        platform.toolchain.ifacewriter.blocks.append(block)
        self.comb += self.cd_eth_rx.clk.eq(eth_rx_clk)

        cmd = "create_clock -period {} auto_eth_rx_clk".format(1e9/125e6)
        platform.toolchain.additional_sdc_commands.append(cmd)

        # TX Clk.
        # -------
        block = {
            "type"       : "GPIO",
            "size"       : 1,
            "location"   : platform.get_pin_location(clock_pads.tx)[0],
            "properties" : platform.get_pin_properties(clock_pads.tx),
            "name"       : "auto_eth_tx_clk_delayed",
            "mode"       : "OUTPUT_CLK"
        }
        platform.toolchain.ifacewriter.blocks.append(block)

        # TX PLL.
        # -------
        self.pll = pll = TRIONPLL(platform, n=1) # FIXME: Add Auto-Numbering.
        pll.register_clkin(None,          freq=125e6,           name="auto_eth_rx_clk")
        pll.create_clkout(None,           freq=125e6,           name="auto_eth_tx_clk")
        pll.create_clkout(self.cd_eth_tx, freq=125e6,  phase=0, name="auto_eth_tx_clk_delayed")

        cmd = "create_clock -period {} eth_tx_clk".format(1e9/125e6)
        platform.toolchain.additional_sdc_commands.append(cmd)

        # Reset.
        # ------
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset(cycles=hw_reset_cycles)
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        if hasattr(clock_pads, "rst_n"):
            self.comb += clock_pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]

# LiteEth PHY RGMII --------------------------------------------------------------------------------

class LiteEthPHYRGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, platform, clock_pads, pads, with_hw_init_reset=True,
            iodelay_clk_freq=200e6, hw_reset_cycles=256):
        self.crg = LiteEthPHYRGMIICRG(platform, clock_pads, with_hw_init_reset, hw_reset_cycles)
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYRGMIITX(platform, pads))
        self.rx  = ClockDomainsRenamer("eth_rx")(LiteEthPHYRGMIIRX(platform, pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)
