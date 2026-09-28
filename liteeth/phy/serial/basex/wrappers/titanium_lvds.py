#
# This file is part of LiteEth.
#
# Copyright (c) 2025 Fin Maaß <f.maass@vogl-electronic.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.build.io import *

from litex.soc.cores.code_8b10b import K, D, Decoder

from litex.soc.cores.clock.efinix import TITANIUMPLL

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import *

from liteeth.phy.serial.basex.pma.titanium_lvds import (
    EfinixSerdesDiffTx,
    EfinixSerdesDiffRx,
    Decoder8b10bChecker,
    Decoder8b10bIdleChecker,
    EfinixAligner,
    EfinixSerdesBuffer,
    EfinixSerdesDiffRxClockRecovery,
    EfinixSerdesClocking,
)

# EfinixTitaniumLVDS_1000BASEX PHY -----------------------------------------------------------------

class EfinixTitaniumLVDS_1000BASEX(LiteXModule):
    dw                = 8
    linerate          = 1.25e9
    rx_clk_freq       = 125e6
    tx_clk_freq       = 125e6
    with_preamble_crc = True
    def __init__(self, pads, refclk=None, refclk_freq=200e6, crg=None, rx_delay=None, with_i2c=True, rx_term=True):
        self.pcs = pcs = PCS(lsb_first=True, eth_tx_clk_freq=self.tx_clk_freq, with_csr=True)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up
        self.ev      = pcs.ev

        # # #

        # Clocking.
        # ---------
        if crg is None:
            assert refclk is not None
            self.crg = crg = EfinixSerdesClocking(
                refclk      = refclk,
                refclk_freq = refclk_freq,
            )
        else:
            self.crg = crg

        # TX.
        # ---
        tx = EfinixSerdesDiffTx(
            data     = pcs.tbi_tx,
            tx_p     = pads.tx_p,
            tx_n     = pads.tx_n,
            clk      = crg.cd_eth_tx.clk,
            fast_clk = crg.cd_eth_trx_fast.clk,
        )
        self.tx = ClockDomainsRenamer("eth_tx")(tx)


        # RX.
        # ---
        rx = EfinixSerdesDiffRxClockRecovery(
            rx_p       = pads.rx_p,
            rx_n       = pads.rx_n,
            data       = pcs.tbi_rx,
            data_valid = pcs.tbi_rx_ce,
            align      = pcs.align,
            clk        = crg.cd_eth_rx.clk,
            fast_clk   = crg.cd_eth_trx_fast.clk,
            delay      = rx_delay,
            rx_term    = rx_term,
        )
        self.comb += rx.reset.eq(pcs.restart)
        self.rx = ClockDomainsRenamer("eth_rx")(rx)

        # I2C.
        # ----
        if with_i2c and hasattr(pads, "scl") and hasattr(pads, "sda"):
            from litei2c import LiteI2C

            self.i2c = LiteI2C(LiteXContext.top.sys_clk_freq, pads=pads)
