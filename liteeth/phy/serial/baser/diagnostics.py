#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex.soc.interconnect.csr import CSRField, CSRStatus, CSRStorage

# BASE-R PHY Diagnostics ---------------------------------------------------------------------------

class LiteEthBASERPHY(LiteXModule):
    """Diagnostics shared by BASE-R PHYs without changing their PMA or clocking."""

    with_prbs = True
    loopback_description = "Transceiver loopback."
    prbs_rate_description = "block rate, linerate/66, times 66 bits per block."

    def add_prbs_counter(self, prbs_errors_width, rx_ce=None):
        self.rx_prbs_pause  = Signal()
        self.rx_prbs_errors = Signal(prbs_errors_width)

        prbs_pause  = Signal()
        prbs_errors = Signal(prbs_errors_width)
        prbs_next   = Signal(prbs_errors_width + 1)

        self.specials += MultiReg(self.rx_prbs_pause, prbs_pause, "eth_rx")
        self.comb += prbs_next.eq(prbs_errors + self.pcs.rx_error_count)

        count_enable = ~prbs_pause
        if rx_ce is not None:
            count_enable = count_enable & rx_ce

        self.sync.eth_rx += If(~self.rx_prbs31_enable,
            prbs_errors.eq(0),
        ).Elif(count_enable,
            If(prbs_next[prbs_errors_width],
                prbs_errors.eq(2**prbs_errors_width - 1),
            ).Else(
                prbs_errors.eq(prbs_next[:prbs_errors_width]),
            ),
        )
        self.specials += MultiReg(prbs_errors, self.rx_prbs_errors)

    def add_csr(self):
        self._reset = CSRStorage(description="PHY reset.")
        self.comb += self.reset.eq(self._reset.storage)

        self._control = CSRStorage(description="PHY control.", fields=[
            CSRField("loopback", size=3, description=self.loopback_description),
            CSRField("tx_prbs31_enable", size=1, description=
                "Transmit PRBS31 test pattern (49.2.8)"),
            CSRField("rx_prbs31_enable", size=1, description=
                "Check received stream against PRBS31 (49.2.12)"),
            CSRField("prbs_pause", size=1, description=
                "Freeze the PRBS31 error counter so that it can be read coherently"),
        ])

        self.specials += [
            MultiReg(self._control.fields.loopback,         self.loopback),
            MultiReg(self._control.fields.tx_prbs31_enable, self.tx_prbs31_enable, "eth_tx"),
            MultiReg(self._control.fields.rx_prbs31_enable, self.rx_prbs31_enable, "eth_rx"),
        ]

        self._status = CSRStatus(description="PHY status.", fields=[
            CSRField("block_lock", size=1, description=
                "Block synchronisation acquired (49.2.9, Figure 49-12)"),
            CSRField("high_ber", size=1, description=
                "Bit error ratio worse than 1e-4 (Figure 49-13)"),
            CSRField("link_up", size=1, description=
                "PCS_status: block lock held and no high BER (49.2.14.1)"),
            CSRField("error_count", size=7, description=
                "PRBS31 bit errors in the last block, valid in receive test-pattern mode"),
            CSRField("block_lock_lost", size=1, description=
                "Block lock has been absent at some point since this register was last read"),
            CSRField("high_ber_latched", size=1, description=
                "high_ber has been asserted at some point since this register was last read"),
        ])

        self.specials += [
            MultiReg(self.pcs.rx_block_lock,  self._status.fields.block_lock),
            MultiReg(self.pcs.rx_high_ber,    self._status.fields.high_ber),
            MultiReg(self.link_up,            self._status.fields.link_up),
            MultiReg(self.pcs.rx_error_count, self._status.fields.error_count),
        ]

        block_lock_lost  = Signal()
        high_ber_latched = Signal()
        self.sync += If(self._status.we,
            block_lock_lost.eq(~self._status.fields.block_lock),
            high_ber_latched.eq(self._status.fields.high_ber),
        ).Else(
            If(~self._status.fields.block_lock, block_lock_lost.eq(1)),
            If(self._status.fields.high_ber, high_ber_latched.eq(1)),
        )
        self.comb += [
            self._status.fields.block_lock_lost.eq(block_lock_lost),
            self._status.fields.high_ber_latched.eq(high_ber_latched),
        ]

        if self.with_prbs:
            self.comb += self.rx_prbs_pause.eq(self._control.fields.prbs_pause)
            self._rx_prbs_errors = CSRStatus(len(self.rx_prbs_errors), description=
                "PRBS31 bit errors since the test was last enabled, saturating rather than wrapping."
                " Set prbs_pause before reading: the counter lives in the receive clock domain and is"
                " only stable while paused. For a bit error ratio, the denominator is the elapsed time"
                " times the " + self.prbs_rate_description)
            self.comb += self._rx_prbs_errors.status.eq(self.rx_prbs_errors)
