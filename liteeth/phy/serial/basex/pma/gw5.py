#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.serial.basex.pma.gw5_config import _serdes_csr

# GW5 Raw SerDes -----------------------------------------------------------------------------------

class GW5SerDes(LiteXModule):
    """Raw 1.25 Gb/s, 10-bit interface on GW5AST-138B Q1 lane 0 or 1.

    The caller supplies the PCS and synchronizes resets to the TX/RX clocks.
    The RX interface can have gaps; rx_valid qualifies rx_data. Neither this
    interface nor hardware comma alignment guarantees deterministic latency.
    rx_fifo_level/tx_fifo_level expose the interface FIFO occupancies for
    latency diagnostics; they are in the RX and TX clock domains.
    """
    def __init__(self, platform, lane=0):
        if platform.devicename != "GW5AST-138B":
            raise ValueError("GW5SerDes currently supports GW5AST-138B.")
        if lane not in (0, 1):
            raise ValueError("GW5SerDes supports Q1 lanes 0 and 1.")
        self.reset    = Signal()
        # GowinSynthesis otherwise merges equivalent registers driven by the
        # independent TX and recovered RX clock outputs of the GTR primitive.
        self.tx_clk   = Signal(attr={("syn_keep", 1)})
        self.rx_clk   = Signal(attr={("syn_keep", 1)})
        self.tx_data  = Signal(10)
        self.rx_data  = Signal(88)
        self.rx_valid = Signal()
        self.rx_empty = Signal()
        self.pll_lock = Signal()
        self.cdr_lock = Signal()
        self.aligned  = Signal()
        self.rx_fifo_level = Signal(5)
        self.tx_fifo_level = Signal(5)

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
        serdes_params.update({
            f"i_FABRIC_LN{lane}_RSTN_I"         : ~self.reset,
            f"i_LANE{lane}_PCS_TX_RST"          : self.reset,
            f"i_LANE{lane}_PCS_RX_RST"          : self.reset,
            f"i_LANE{lane}_FABRIC_TX_CLK"       : self.tx_clk,
            f"i_LANE{lane}_FABRIC_RX_CLK"       : self.rx_clk,
            f"i_FABRIC_LN{lane}_TXDATA_I"       : Cat(self.tx_data, Constant(0, 70)),
            f"i_FABRIC_LN{lane}_TX_VLD_IN"      : 1,
            f"i_LANE{lane}_RX_IF_FIFO_RDEN"     : ~self.rx_empty,
            f"o_LANE{lane}_PCS_TX_O_FABRIC_CLK" : self.tx_clk,
            f"o_LANE{lane}_PCS_RX_O_FABRIC_CLK" : self.rx_clk,
            f"o_FABRIC_LN{lane}_RXDATA_O"       : self.rx_data,
            f"o_FABRIC_LN{lane}_RX_VLD_OUT"     : self.rx_valid,
            f"o_LANE{lane}_RX_IF_FIFO_EMPTY"    : self.rx_empty,
            f"o_FABRIC_LANE{lane}_CMU_OK_O"     : self.pll_lock,
            f"o_FABRIC_LN{lane}_PMA_RX_LOCK_O"  : self.cdr_lock,
            f"o_LANE{lane}_ALIGN_LINK"          : self.aligned,
            f"o_LANE{lane}_RX_IF_FIFO_RDUSEWD"  : self.rx_fifo_level,
            f"o_LANE{lane}_TX_IF_FIFO_WRUSEWD"  : self.tx_fifo_level,
        })
        self.specials += Instance("GTR12_QUAD", **serdes_params)

        # Configuration ----------------------------------------------------------------------------
        # Write the embedded configuration in the gateware directory when Gowin runs.
        platform.toolchain.additional_tcl_commands += [
            'set serdes_csr [open "gw5_1000basex.csr" w]',
            'puts -nonewline $serdes_csr {' + _serdes_csr[lane] + '}',
            'close $serdes_csr',
            'set_csr gw5_1000basex.csr',
        ]
