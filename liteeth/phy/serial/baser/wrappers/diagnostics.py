#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect.csr import CSRField, CSRStatus, CSRStorage

# BASE-R PHY Diagnostics ---------------------------------------------------------------------------

class LiteEthBASERPHY(LiteXModule):
    """Diagnostics shared by BASE-R PHYs without changing their PMA or clocking."""

    with_prbs = True
    loopback_description = "Transceiver loopback."
    prbs_rate_description = "block rate, linerate/66, times 66 bits per block."

    def add_prbs_counter(self, prbs_errors_width, rx_ce=None):
        if prbs_errors_width < 1:
            raise ValueError("PRBS error counter width must be positive.")
        self.rx_prbs_pause  = Signal()
        self.rx_prbs_paused = Signal()
        self.rx_prbs_errors = Signal(prbs_errors_width)

        prbs_pause  = Signal()
        prbs_paused = Signal()
        prbs_errors = Signal(prbs_errors_width)
        prbs_next   = Signal(max(prbs_errors_width, len(self.pcs.rx_error_count)) + 1)

        self.specials += MultiReg(self.rx_prbs_pause, prbs_pause, "eth_rx")
        self.comb += prbs_next.eq(prbs_errors + self.pcs.rx_error_count)

        count_enable = ~prbs_pause
        if rx_ce is not None:
            count_enable = count_enable & rx_ce

        self.sync.eth_rx += prbs_paused.eq(prbs_pause)
        self.sync.eth_rx += If(~self.rx_prbs31_enable,
            prbs_errors.eq(0),
        ).Elif(count_enable,
            If(prbs_next[prbs_errors_width:] != 0,
                prbs_errors.eq(2**prbs_errors_width - 1),
            ).Else(
                prbs_errors.eq(prbs_next[:prbs_errors_width]),
            ),
        )
        # Transfer the count and pause acknowledgment as one word. Acknowledging a pause
        # therefore also confirms that the final frozen count has reached sys.
        self.prbs_snapshot = BusSynchronizer(prbs_errors_width + 1, "eth_rx", "sys")
        self.comb += [
            self.prbs_snapshot.i.eq(Cat(prbs_errors, prbs_paused)),
            self.rx_prbs_errors.eq(self.prbs_snapshot.o[:prbs_errors_width]),
            self.rx_prbs_paused.eq(self.prbs_snapshot.o[prbs_errors_width]),
        ]

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
                "Freeze the PRBS31 error counter; wait for prbs_paused before reading"),
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
                "PRBS31 bit errors in a sampled receive block, valid in receive test-pattern mode"),
            CSRField("block_lock_lost", size=1, description=
                "Block lock has been absent at some point since this register was last read"),
            CSRField("high_ber_latched", size=1, description=
                "high_ber has been asserted at some point since this register was last read"),
            CSRField("prbs_paused", size=1, offset=12, description=
                "The paused PRBS31 counter snapshot has reached sys; zero without PRBS support"),
        ])

        self.specials += [
            MultiReg(self.pcs.rx_block_lock,  self._status.fields.block_lock),
            MultiReg(self.pcs.rx_high_ber,    self._status.fields.high_ber),
            MultiReg(self.link_up,            self._status.fields.link_up),
        ]
        self.error_count_cdc = BusSynchronizer(len(self.pcs.rx_error_count), "eth_rx", "sys")
        self.comb += [
            self.error_count_cdc.i.eq(self.pcs.rx_error_count),
            self._status.fields.error_count.eq(self.error_count_cdc.o),
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
            self.comb += [
                self.rx_prbs_pause.eq(self._control.fields.prbs_pause),
                self._status.fields.prbs_paused.eq(self.rx_prbs_paused),
            ]
            self._rx_prbs_errors = CSRStatus(len(self.rx_prbs_errors), description=
                "PRBS31 bit errors since the test was last enabled, saturating rather than wrapping."
                " Set prbs_pause and wait for prbs_paused before reading all words. Clear prbs_pause"
                " and wait for prbs_paused to clear before requesting another snapshot. Disabling"
                " the checker clears the counter, even while paused. For a bit error ratio, the denominator is the elapsed time"
                " times the " + self.prbs_rate_description)
            self.comb += self._rx_prbs_errors.status.eq(self.rx_prbs_errors)
        else:
            self.comb += self._status.fields.prbs_paused.eq(0)
