#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from liteeth.phy.pcs_10g.encoder import XGMIIBaseREncoder
from liteeth.phy.pcs_10g.prbs import PRBS31Generator
from liteeth.phy.pcs_10g.scrambler import Scrambler

# PCS TX Interface ---------------------------------------------------------------------------------

class PCSTXInterface(LiteXModule):
    """Transmit side of the PCS/PMA boundary (IEEE 802.3ae Clause 49.2.6, 49.2.8)."""
    def __init__(self, dw=64, hdr_width=2, prbs31_enable=False):
        self.encoded_tx_data = Signal(dw)
        self.encoded_tx_hdr  = Signal(hdr_width)

        self.serdes_tx_data  = Signal(dw)
        self.serdes_tx_hdr   = Signal(hdr_width)

        # Configuration input
        self.cfg_tx_prbs31_enable = Signal()

        # Scrambler (49.2.6). Header bypasses it (49.2.4.3), only payload is scrambled
        self.scrambler = scrambler = Scrambler(dw)
        self.comb += scrambler.data_in.eq(self.encoded_tx_data)

        payload = scrambler.data_out

        data = Signal(dw, reset_less=True)
        hdr  = Signal(hdr_width, reset_less=True)

        if prbs31_enable:
            self.prbs31 = prbs31 = PRBS31Generator(dw + hdr_width)
            self.comb += prbs31.enable.eq(self.cfg_tx_prbs31_enable)
            self.sync += If(self.cfg_tx_prbs31_enable,
                # The test pattern replaces the block outright, header included.
                hdr.eq( prbs31.data_out[0:hdr_width]),
                data.eq(prbs31.data_out[hdr_width:hdr_width + dw]),
            ).Else(
                data.eq(payload),
                hdr.eq(self.encoded_tx_hdr),
            )
        else:
            self.sync += [
                data.eq(payload),
                hdr.eq(self.encoded_tx_hdr),
            ]

        # Bit reverse for transceiver gearbox
        data_r = Signal(dw)
        hdr_r  = Signal(hdr_width)
        self.comb += [
            data_r.eq(Cat(*[data[dw - 1 - n]       for n in range(dw)])),
            hdr_r.eq( Cat(*[hdr[hdr_width - 1 - n] for n in range(hdr_width)])),
        ]
        data, hdr = data_r, hdr_r

        self.comb += [
            self.serdes_tx_data.eq(data),
            self.serdes_tx_hdr.eq(hdr),
        ]

# PCS TX -------------------------------------------------------------------------------------------

class PCSTX(LiteXModule):
    """Complete PCS transmit path (IEEE 802.3ae Clause 49.2.4 - 49.2.8)."""
    def __init__(self, dw=64, hdr_width=2, prbs31_enable=False):
        self.xgmii_txd  = Signal(dw)
        self.xgmii_txc  = Signal(dw//8)

        self.serdes_tx_data = Signal(dw)
        self.serdes_tx_hdr  = Signal(hdr_width)

        # Status output
        self.tx_bad_block = Signal()

        # Configuration input
        self.cfg_tx_prbs31_enable = Signal()

        self.encoder = encoder = XGMIIBaseREncoder()
        self.interface = interface = PCSTXInterface(
            dw            = dw,
            hdr_width     = hdr_width,
            prbs31_enable = prbs31_enable,
        )

        self.comb += [
            encoder.xgmii_txd.eq(self.xgmii_txd),
            encoder.xgmii_txc.eq(self.xgmii_txc),

            interface.encoded_tx_data.eq(encoder.encoded_tx_data),
            interface.encoded_tx_hdr.eq(encoder.encoded_tx_hdr),
            interface.cfg_tx_prbs31_enable.eq(self.cfg_tx_prbs31_enable),

            self.serdes_tx_data.eq(interface.serdes_tx_data),
            self.serdes_tx_hdr.eq(interface.serdes_tx_hdr),
            self.tx_bad_block.eq(encoder.tx_bad_block),
        ]
