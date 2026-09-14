#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField

from liteeth.common import *
from liteeth.phy.pcs_1000basex import PCS

# GW5 1000BASE-X PHY -------------------------------------------------------------------------------

class GW5_1000BASEX(LiteXModule):
    """GW5AST-138B 1000BASE-X PHY on Q1 lane 0, using a 100 MHz Q1 REFCLK1.

    The hard SerDes uses a raw 10-bit interface at 125 MHz. LiteEth implements the
    8b/10b PCS and autonegotiation; the SerDes performs comma alignment. The attached
    CSR configuration initializes the analog/clocking blocks during FPGA configuration.
    Dedicated serial and reference-clock pins are selected by the SerDes configuration,
    as in Gowin's generated GTR12_QUAD wrapper.
    """
    dw          = 8
    linerate    = 1.25e9
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6

    def __init__(self, platform, with_csr=True, pcs_kwargs=None):
        if platform.devicename != "GW5AST-138B":
            raise ValueError("GW5_1000BASEX currently supports GW5AST-138B, Q1 lane 0.")

        self.reset     = Signal()
        self.pll_lock  = Signal()
        self.cdr_lock  = Signal()
        self.aligned   = Signal()
        self.cd_eth_tx = ClockDomain()
        self.cd_eth_rx = ClockDomain()

        # PCS --------------------------------------------------------------------------------------
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS(lsb_first=True, **pcs_kwargs)
        self.link_up = pcs.link_up

        # Register the MAC boundary to keep the 125 MHz PCS paths local.
        self.tx_buffer = tx_buffer = ClockDomainsRenamer("eth_tx")(
            stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
        self.rx_buffer = rx_buffer = ClockDomainsRenamer("eth_rx")(
            stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
        self.sink   = tx_buffer.sink
        self.source = rx_buffer.source
        self.comb += [
            tx_buffer.source.connect(pcs.sink),
            pcs.source.connect(rx_buffer.sink),
        ]

        # SerDes Datapath --------------------------------------------------------------------------
        tx_data  = Signal(80)
        rx_data  = Signal(88)
        rx_valid = Signal()
        rx_empty = Signal()
        self.sync.eth_tx += tx_data.eq(pcs.tbi_tx)
        self.sync.eth_rx += [
            pcs.tbi_rx.eq(rx_data[:10]),
            pcs.tbi_rx_ce.eq(rx_valid),
        ]

        # Clocking / Reset -------------------------------------------------------------------------
        reset = self.reset | ResetSignal("sys")
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset | ~self.pll_lock),
            AsyncResetSynchronizer(self.cd_eth_rx, reset | ~self.cdr_lock),
        ]

        # SerDes -----------------------------------------------------------------------------------
        # Unused fabric controls are tied low; the CSR configuration selects their internal controls.
        serdes_params = dict(
            p_POSITION                    = "Q1",
            i_FABRIC_CLK_LIFE_DIV_I       = Constant(0, 2),
            i_FABRIC_CM0_RXCLK_OE_L_I     = Constant(0, 1),
            i_FABRIC_CM0_RXCLK_OE_R_I     = Constant(0, 1),
            i_FABRIC_PMA_PD_REFHCLK_I     = Constant(0, 1),
            i_FABRIC_REFCLK1_INPUT_SEL_I  = Constant(0, 3),
            i_FABRIC_REFCLK_INPUT_SEL_I   = Constant(0, 3),
            i_FABRIC_REFCLK_OE_L_I        = Constant(0, 1),
            i_FABRIC_REFCLK_OE_R_I        = Constant(0, 1),
            i_FABRIC_REFCLK_OUTPUT_SEL_I  = Constant(0, 5),
            i_REFCLKM0_I                  = Constant(0, 1),
            i_REFCLKM1_I                  = Constant(0, 1),
            i_REFCLKP0_I                  = Constant(0, 1),
            i_REFCLKP1_I                  = Constant(0, 1),
            i_FABRIC_BURN_IN_I            = Constant(0, 1),
            i_FABRIC_CK_SOC_DIV_I         = Constant(0, 2),
            i_FABRIC_CLK_REF_CORE_I       = Constant(0, 1),
            i_FABRIC_CMU1_REFCLK_GATE_I   = Constant(0, 1),
            i_FABRIC_CMU_REFCLK_GATE_I    = Constant(0, 1),
            i_FABRIC_GLUE_MAC_INIT_INFO_I = Constant(0, 1),
            i_FABRIC_REFCLK_GATE_I        = Constant(0, 1),
            i_FABRIC_CMU0_RESETN_I        = Constant(0, 1),
            i_FABRIC_CMU0_PD_I            = Constant(0, 1),
            i_FABRIC_CMU0_IDDQ_I          = Constant(0, 1),
            i_FABRIC_CMU1_RESETN_I        = Constant(0, 1),
            i_FABRIC_CMU1_PD_I            = Constant(0, 1),
            i_FABRIC_CMU1_IDDQ_I          = Constant(0, 1),
            i_FABRIC_PLL_CDN_I            = Constant(0, 1),
            i_FABRIC_CM1_PD_REFCLK_DET_I  = Constant(0, 1),
            i_FABRIC_CM0_PD_REFCLK_DET_I  = Constant(0, 1),
            i_FABRIC_POR_N_I              = Constant(0, 1),
            i_FABRIC_QUAD_MCU_REQ_I       = Constant(0, 1),
            i_CK_AHB_I                    = Constant(0, 1),
            i_AHB_RSTN                    = Constant(0, 1),
            i_TEST_DEC_EN                 = Constant(0, 1),
            i_QUAD_PCIE_CLK               = Constant(0, 1),
            i_PCIE_DIV2_REG               = Constant(0, 1),
            i_PCIE_DIV4_REG               = Constant(0, 1),
            i_PMAC_LN_RSTN                = Constant(0, 1),
        )
        for n in range(4):
            serdes_params.update({
                f"i_LN{n}_RXM_I"                : Constant(0, 1),
                f"i_LN{n}_RXP_I"                : Constant(0, 1),
                f"i_FABRIC_LN{n}_CTRL_I"        : Constant(0, 43),
                f"i_FABRIC_LN{n}_IDDQ_I"        : Constant(0, 1),
                f"i_FABRIC_LN{n}_PD_I"          : Constant(0, 3),
                f"i_FABRIC_LN{n}_RATE_I"        : Constant(0, 2),
                f"i_FABRIC_LN{n}_RSTN_I"        : Constant(0, 1),
                f"i_FABRIC_LN{n}_TXDATA_I"      : Constant(0, 80),
                f"i_LANE{n}_PCS_RX_RST"         : Constant(0, 1),
                f"i_LANE{n}_ALIGN_TRIGGER"      : Constant(0, 1),
                f"i_LANE{n}_CHBOND_START"       : Constant(0, 1),
                f"i_LANE{n}_PCS_TX_RST"         : Constant(0, 1),
                f"i_LANE{n}_FABRIC_RX_CLK"      : Constant(0, 1),
                f"i_LANE{n}_FABRIC_C2I_CLK"     : Constant(0, 1),
                f"i_LANE{n}_FABRIC_TX_CLK"      : Constant(0, 1),
                f"i_LANE{n}_RX_IF_FIFO_RDEN"    : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_RESETN_I" : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_PD_I"     : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_IDDQ_I"   : Constant(0, 1),
                f"i_FABRIC_LN{n}_CTRL_I_H"      : Constant(0, 43),
                f"i_FABRIC_LN{n}_PD_I_H"        : Constant(0, 3),
                f"i_FABRIC_LN{n}_RATE_I_H"      : Constant(0, 2),
                f"i_FABRIC_LN{n}_TX_VLD_IN"     : Constant(0, 1),
            })
        serdes_params.update(
            i_FABRIC_LN0_RSTN_I         = ~reset,
            i_LANE0_PCS_TX_RST          = reset,
            i_LANE0_PCS_RX_RST          = reset,
            i_LANE0_FABRIC_TX_CLK       = ClockSignal("eth_tx"),
            i_LANE0_FABRIC_RX_CLK       = ClockSignal("eth_rx"),
            i_FABRIC_LN0_TXDATA_I       = tx_data,
            i_FABRIC_LN0_TX_VLD_IN      = 1,
            i_LANE0_RX_IF_FIFO_RDEN     = ~rx_empty,
            o_LANE0_PCS_TX_O_FABRIC_CLK = self.cd_eth_tx.clk,
            o_LANE0_PCS_RX_O_FABRIC_CLK = self.cd_eth_rx.clk,
            o_FABRIC_LN0_RXDATA_O       = rx_data,
            o_FABRIC_LN0_RX_VLD_OUT     = rx_valid,
            o_LANE0_RX_IF_FIFO_EMPTY    = rx_empty,
            o_FABRIC_LANE0_CMU_OK_O     = self.pll_lock,
            o_FABRIC_LN0_PMA_RX_LOCK_O  = self.cdr_lock,
            o_LANE0_ALIGN_LINK          = self.aligned,
        )
        self.specials += Instance("GTR12_QUAD", **serdes_params)

        config = os.path.splitext(os.path.abspath(__file__))[0] + ".csr"
        platform.toolchain.additional_tcl_commands.append("set_csr {" + config.replace("\\", "/") + "}")

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self._status = CSRStatus(fields=[
            CSRField("pll_lock", description="Transmit PLL locked."),
            CSRField("cdr_lock", description="Receive clock recovery locked."),
            CSRField("aligned",  description="Receive comma alignment established."),
            CSRField("link_up",  description="PCS autonegotiation completed."),
        ])
        self.comb += self.reset.eq(self._reset.storage)
        self.specials += MultiReg(Cat(self.pll_lock, self.cdr_lock, self.aligned, self.link_up),
            self._status.status)
