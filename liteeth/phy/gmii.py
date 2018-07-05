from liteeth.common import *

from migen.genlib.io import DDROutput
from migen.genlib.resetsync import AsyncResetSynchronizer

from liteeth.phy.common import *


class LiteEthPHYGMIITX(Module):
    def __init__(self, pads):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))

        # # #

        if hasattr(pads, "tx_er"):
            pads.tx_er.reset_less = True
            self.sync += pads.tx_er.eq(0)
        pads.tx_en.reset_less = True
        pads.tx_data.reset_less = True
        self.sync += [
            pads.tx_en.eq(sink.valid),
            pads.tx_data.eq(sink.data),
            sink.ready.eq(1)
        ]


class LiteEthPHYGMIIRX(Module):
    def __init__(self, pads):
        self.source = source = stream.Endpoint(eth_phy_description(8))

        # # #

        dv_d = Signal()
        self.sync += [
            dv_d.eq(pads.rx_dv),
            source.valid.eq(pads.rx_dv),
            source.data.eq(pads.rx_data)
        ]
        self.comb += source.last.eq(~pads.rx_dv & dv_d)


class LiteEthPHYGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset, mii_mode=0):
        self._reset = CSRStorage()

        # # #

        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()

        # RX : Let the synthesis tool insert the appropriate clock buffer
        self.comb += self.cd_eth_rx.clk.eq(clock_pads.rx)

        # TX : GMII: Drive clock_pads.gtx, clock_pads.tx unused
        #      MII: Use PHY clock_pads.tx as eth_tx_clk, do not drive clock_pads.gtx
        self.specials += DDROutput(1, mii_mode, clock_pads.gtx, ClockSignal("eth_tx"))
        if isinstance(mii_mode, int) and (mii_mode == 0):
            self.comb += self.cd_eth_tx.clk.eq(self.cd_eth_rx.clk)
        else:
            # XXX Xilinx specific, replace BUFGMUX with a generic clock buffer?
            self.specials += Instance("BUFGMUX",
                                      i_I0=self.cd_eth_rx.clk,
                                      i_I1=clock_pads.tx,
                                      i_S=mii_mode,
                                      o_O=self.cd_eth_tx.clk)

        reset = Signal()
        if with_hw_init_reset:
            self.submodules.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)

        self.comb += pads.rst_n.eq(~reset)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]


class LiteEthPHYGMII(Module, AutoCSR):
    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        self.dw = 8
        self.submodules.crg = LiteEthPHYGMIICRG(clock_pads, pads, with_hw_init_reset)
        self.submodules.tx = ClockDomainsRenamer("eth_tx")(LiteEthPHYGMIITX(pads))
        self.submodules.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHYGMIIRX(pads))
        self.sink, self.source = self.tx.sink, self.rx.source

        if hasattr(pads, "mdc"):
            self.submodules.mdio = LiteEthPHYMDIO(pads)
