#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.serial.gw5_serdes import gw5_check_lane, gw5_gtr12_quad_params, gw5_add_serdes_csr
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
        gw5_check_lane(platform, lane, "GW5SerDes")
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
        serdes_params = gw5_gtr12_quad_params()
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
        gw5_add_serdes_csr(platform, "gw5_1000basex.csr", _serdes_csr[lane])
