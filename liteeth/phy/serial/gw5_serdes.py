#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

# GW5 GTR12 Helpers --------------------------------------------------------------------------------

def gw5_check_lane(platform, lane, name):
    if platform.devicename != "GW5AST-138B":
        raise ValueError(f"{name} currently supports GW5AST-138B.")
    if lane not in (0, 1):
        raise ValueError(f"{name} supports Q1 lanes 0 and 1.")


def gw5_gtr12_quad_params():
    """GTR12_QUAD parameters with all lanes and fabric controls tied off.

    Unused fabric controls are tied low; the CSR configuration selects their internal controls.
    The caller overrides the entries of the lane it uses.
    """
    params = dict(
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
        params.update({
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
    return params


def gw5_add_serdes_csr(platform, filename, csr):
    """Write the embedded SerDes configuration in the gateware directory when Gowin runs."""
    platform.toolchain.additional_tcl_commands += [
        f'set serdes_csr [open "{filename}" w]',
        'puts -nonewline $serdes_csr {' + csr + '}',
        'close $serdes_csr',
        f'set_csr {filename}',
    ]
