#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from liteeth.phy.serial.gw5_serdes import gw5_check_lane, gw5_gtr12_quad_params, gw5_add_serdes_csr
from liteeth.phy.serial.baser.pma.gw5_config import _serdes_csr

# Helpers ------------------------------------------------------------------------------------------

def _wire_block(data, header):
    """66-bit block in transmission order (bit 0 first) from the PCS data/header.

    The BASE-R PCS uses the transceiver-gearbox convention: header MSB first, then data MSB first.
    """
    return Cat(*[header[1 - n] for n in range(2)], *[data[63 - n] for n in range(64)])

# 66b -> 64b TX Gearbox ----------------------------------------------------------------------------

class GW5BASERTXGearbox(LiteXModule):
    """66-bit blocks to a continuous 64-bit word stream (bit 0 transmitted first).

    One 64-bit word is produced per cycle; a block is consumed on 32 of every 33 cycles (ce).
    The PCS must only advance when ce is set. Blocks are shifted into a two-block window at fixed
    positions; only the small word offset has feedback, and the word is extracted from the window
    through two registered mux stages (multiples of 8 bits, then 0-7 bits).
    """
    def __init__(self):
        self.data   = Signal(64)
        self.header = Signal(2)
        self.ce     = Signal()
        self.output = Signal(64)

        # # #

        window      = Signal(132) # Two blocks, older block first.
        offset      = Signal(7)   # Next word offset in window (even, 0-68).
        offset_next = Signal(8)
        self.comb += offset_next.eq(offset + 64)
        # ce is registered and set on the cycles that load the PCS block into the window.
        self.sync += [
            If(offset_next > 68,
                window.eq(Cat(window[66:], _wire_block(self.data, self.header))),
                offset.eq(offset_next - 66),
                self.ce.eq(offset_next - 66 > 4),
            ).Else(
                offset.eq(offset_next),
                self.ce.eq(offset_next > 4),
            )
        ]

        # Word extraction.
        coarse = Signal(64 + 7)
        fine   = Signal(3)
        window_padded = Cat(window, Constant(0, 4))
        self.sync += [
            Case(offset[3:], {n: coarse.eq(window_padded[8*n:8*n + 71]) for n in range(9)}),
            fine.eq(offset[:3]),
        ]
        self.sync += Case(fine, {n: self.output.eq(coarse[n:n + 64]) for n in range(8)})

# 64b -> 66b RX Gearbox ----------------------------------------------------------------------------

class GW5BASERRXGearbox(LiteXModule):
    """Continuous 64-bit word stream (bit 0 received first) to 66-bit blocks.

    A block is produced on 32 of every 33 valid input words (ce). A slip pulse drops one received
    bit, moving the block boundary by one bit for the PCS block synchronization. Words are shifted
    into a window at fixed positions; only the small block offset has feedback, and the block is
    extracted from the window through two registered mux stages.
    """
    def __init__(self):
        self.input       = Signal(64)
        self.input_valid = Signal(reset=1)
        self.slip        = Signal()
        self.data        = Signal(64)
        self.header      = Signal(2)
        self.ce          = Signal()

        # # #

        window = Signal(192) # Three words, oldest word first.
        offset = Signal(7)   # Next block offset in window (0-66).
        output = Signal()
        slip   = Signal()
        self.comb += output.eq(offset < 64)
        self.sync += [
            # Keep slip requests until applied.
            If(self.slip, slip.eq(1)),
            If(self.input_valid,
                window.eq(Cat(window[64:], self.input)),
                slip.eq(0),
                If(output,
                    offset.eq(offset + 2 + (slip | self.slip)),
                ).Else(
                    offset.eq(offset - 64 + (slip | self.slip)),
                )
            )
        ]

        # Block extraction (from the window before the shift, captured with offset).
        fine    = Signal(3)
        valid_d = Signal()
        coarse  = Signal(66 + 7)
        self.sync += [
            If(self.input_valid,
                Case(offset[3:], {n: coarse.eq(window[8*n:8*n + 73]) for n in range(8)}),
            ),
            fine.eq(offset[:3]),
            valid_d.eq(self.input_valid & output),
        ]
        block = Signal(66)
        self.sync += [
            Case(fine, {n: block.eq(coarse[n:n + 66]) for n in range(8)}),
            self.ce.eq(valid_d),
        ]
        self.comb += [
            Cat(*[self.header[1 - n] for n in range(2)]).eq(block[:2]),
            Cat(*[self.data[63 - n]  for n in range(64)]).eq(block[2:]),
        ]

# GW5 10GBASE-R PMA --------------------------------------------------------------------------------

class PMA_GW5_10G_BASER(LiteXModule):
    """10.3125 Gb/s GW5AST-138B GTR12 PMA on Q1 lane 0 or 1, using a 125 MHz Q1 REFCLK0.

    The GTR12 provides a raw 64-bit interface (16-bit internal width, 1:4 ratio) at linerate/64.
    GW5AST-138 lanes have no hard 64b/66b gearbox: soft gearboxes adapt the 66-bit PCS blocks, so
    the PCS advances on tx_ce/rx_ce (32 of every 33 cycles), as with a gapped transceiver gearbox.
    """
    def __init__(self, platform, lane=0):
        gw5_check_lane(platform, lane, "PMA_GW5_10G_BASER")
        self.reset     = Signal()
        self.tx_data   = Signal(64)
        self.tx_header = Signal(2)
        self.tx_ce     = Signal()
        self.rx_data   = Signal(64)
        self.rx_header = Signal(2)
        self.rx_ce     = Signal()
        self.rx_slip   = Signal()
        self.pll_lock  = Signal()
        self.cdr_lock  = Signal()
        self.rx_fifo_level = Signal(5)
        self.tx_fifo_level = Signal(5)

        self.cd_eth_tx = ClockDomain()
        self.cd_eth_rx = ClockDomain()

        # # #

        # GowinSynthesis otherwise merges equivalent registers driven by the independent TX and
        # recovered RX clock outputs of the GTR primitive; this also keeps the constrained nets.
        tx_clk   = self.cd_eth_tx.clk
        rx_clk   = self.cd_eth_rx.clk
        tx_clk.attr.add(("syn_keep", 1))
        rx_clk.attr.add(("syn_keep", 1))
        tx_data  = Signal(80)
        rx_data  = Signal(88)
        rx_valid = Signal()
        rx_empty = Signal()

        # Clocking / Reset -------------------------------------------------------------------------
        reset = self.reset | ResetSignal("sys")
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset | ~self.pll_lock),
            AsyncResetSynchronizer(self.cd_eth_rx, reset | ~self.cdr_lock),
        ]

        # TX Gearbox -------------------------------------------------------------------------------
        self.tx_gearbox = tx_gearbox = ClockDomainsRenamer("eth_tx")(GW5BASERTXGearbox())
        self.comb += [
            tx_gearbox.data.eq(self.tx_data),
            tx_gearbox.header.eq(self.tx_header),
            self.tx_ce.eq(tx_gearbox.ce),
        ]
        # The 64-bit word occupies the low 8 bits of eight 10-bit slots, slot 0 transmitted first.
        self.sync.eth_tx += tx_data.eq(Cat(*[Cat(tx_gearbox.output[8*n:8*(n + 1)], Constant(0, 2))
            for n in range(8)]))

        # RX Gearbox -------------------------------------------------------------------------------
        self.rx_gearbox = rx_gearbox = ClockDomainsRenamer("eth_rx")(GW5BASERRXGearbox())
        self.sync.eth_rx += [
            rx_gearbox.input.eq(Cat(*[rx_data[10*n:10*n + 8] for n in range(8)])),
            rx_gearbox.input_valid.eq(rx_valid),
        ]
        self.comb += [
            rx_gearbox.slip.eq(self.rx_slip),
            self.rx_data.eq(rx_gearbox.data),
            self.rx_header.eq(rx_gearbox.header),
            self.rx_ce.eq(rx_gearbox.ce),
        ]

        # SerDes -----------------------------------------------------------------------------------
        serdes_params = gw5_gtr12_quad_params()
        serdes_params.update({
            f"i_FABRIC_LN{lane}_RSTN_I"         : ~reset,
            f"i_LANE{lane}_PCS_TX_RST"          : reset,
            f"i_LANE{lane}_PCS_RX_RST"          : reset,
            f"i_LANE{lane}_FABRIC_TX_CLK"       : tx_clk,
            f"i_LANE{lane}_FABRIC_RX_CLK"       : rx_clk,
            f"i_FABRIC_LN{lane}_TXDATA_I"       : tx_data,
            f"i_FABRIC_LN{lane}_TX_VLD_IN"      : 1,
            f"i_LANE{lane}_RX_IF_FIFO_RDEN"     : ~rx_empty,
            f"o_LANE{lane}_PCS_TX_O_FABRIC_CLK" : tx_clk,
            f"o_LANE{lane}_PCS_RX_O_FABRIC_CLK" : rx_clk,
            f"o_FABRIC_LN{lane}_RXDATA_O"       : rx_data,
            f"o_FABRIC_LN{lane}_RX_VLD_OUT"     : rx_valid,
            f"o_LANE{lane}_RX_IF_FIFO_EMPTY"    : rx_empty,
            f"o_FABRIC_LANE{lane}_CMU_OK_O"     : self.pll_lock,
            f"o_FABRIC_LN{lane}_PMA_RX_LOCK_O"  : self.cdr_lock,
            f"o_LANE{lane}_RX_IF_FIFO_RDUSEWD"  : self.rx_fifo_level,
            f"o_LANE{lane}_TX_IF_FIFO_WRUSEWD"  : self.tx_fifo_level,
        })
        self.specials += Instance("GTR12_QUAD", **serdes_params)

        # Configuration ----------------------------------------------------------------------------
        gw5_add_serdes_csr(platform, "gw5_10gbaser.csr", _serdes_csr[lane])
