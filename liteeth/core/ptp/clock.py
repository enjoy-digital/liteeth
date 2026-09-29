#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


from migen import *

from litex.gen import LiteXModule


class LiteEthTSU(LiteXModule):
    """
    Time Stamping Unit (48-bit seconds, 32-bit nanoseconds).

    Addend-based tick accumulation with pipelined multiply and registered
    tick_inc to meet timing. Supports offset correction (±1s) and coarse
    step for initial lock.

    Parameters:
    - clk_freq : System clock frequency for default addend calculation.
    """
    def __init__(self, clk_freq):
        # Time Registers.
        # ---------------
        self.seconds     = Signal(48)
        self.nanoseconds = Signal(32)
        addend_frac_bits = 20
        default_addend   = int(((1 << (32 + addend_frac_bits)) + (clk_freq // 2)) // clk_freq)
        self.addend      = Signal(32, reset=default_addend >> addend_frac_bits)
        self.addend_frac = Signal(addend_frac_bits, reset=default_addend & ((1 << addend_frac_bits) - 1))
        self.offset      = Signal((81, True))
        self.step        = Signal()
        self.step_target = Signal(80)

        # Timestamp Latches.
        # ------------------
        self.rx_ts     = Signal(80)
        self.tx_ts     = Signal(80)
        self.rx_latch  = Signal()
        self.tx_latch  = Signal()

        # # #

        # Tick Accumulation.
        # ------------------
        # Pipelined: addend → multiply | reg | frac add | reg | ns add.
        addend_frac_bits = len(self.addend_frac)
        full_addend_bits = len(self.addend) + addend_frac_bits
        full_addend      = Signal(full_addend_bits)
        inc_nsec_q       = Signal(full_addend_bits + 32)
        inc_nsec_q_r     = Signal(full_addend_bits + 32)
        frac             = Signal(32 + addend_frac_bits)
        frac_sum         = Signal(len(inc_nsec_q) + 1)
        tick_inc         = Signal(34)
        tick_inc_r       = Signal(34)
        tick_nsec        = Signal(34)
        self.comb += [
            full_addend.eq(Cat(self.addend_frac, self.addend)),
            inc_nsec_q.eq(full_addend * int(1_000_000_000)),
            frac_sum.eq(frac + inc_nsec_q_r),
            tick_inc.eq(frac_sum[32 + addend_frac_bits:32 + addend_frac_bits + len(tick_inc)]),
            tick_nsec.eq(self.nanoseconds + tick_inc_r),
        ]
        self.sync += [
            inc_nsec_q_r.eq(inc_nsec_q),
            tick_inc_r.eq(tick_inc),
        ]

        # Offset Correction.
        # ------------------
        offset_nsec = Signal((34, True))
        self.comb += offset_nsec.eq(self.nanoseconds + self.offset)

        # Time Update.
        # ------------
        self.sync += [
            # Coarse Step (initial lock).
            If(self.step,
                self.nanoseconds.eq(self.step_target[0:32]),
                self.seconds.eq(self.step_target[32:80]),
                frac.eq(0),
                self.offset.eq(0),
            # Offset Correction (phase/seconds adjust).
            ).Elif(self.offset != 0,
                If(offset_nsec < 0,
                    self.nanoseconds.eq(offset_nsec + 1_000_000_000),
                    self.seconds.eq(self.seconds - 1),
                ).Elif(offset_nsec >= 1_000_000_000,
                    self.nanoseconds.eq(offset_nsec - 1_000_000_000),
                    self.seconds.eq(self.seconds + 1),
                ).Else(
                    self.nanoseconds.eq(offset_nsec),
                ),
                self.offset.eq(0),
            # Normal Tick.
            ).Else(
                frac.eq(frac_sum[0:32 + addend_frac_bits]),
                If(tick_nsec >= 1_000_000_000,
                    self.nanoseconds.eq(tick_nsec - 1_000_000_000),
                    self.seconds.eq(self.seconds + 1),
                ).Else(
                    self.nanoseconds.eq(tick_nsec),
                ),
            ),
        ]

        # Timestamp Latch.
        # ----------------
        def pack80(sec, nsec):
            return Cat(nsec, sec)
        self.sync += [
            If(self.rx_latch, self.rx_ts.eq(pack80(self.seconds, self.nanoseconds))),
            If(self.tx_latch, self.tx_ts.eq(pack80(self.seconds, self.nanoseconds))),
        ]



class LiteEthPTPRxTimestamp(LiteXModule):
    """RX Timestamp Latch Helper. Detects first beat of Event/General packets for TSU RX latch."""
    def __init__(self, event_port, general_port):
        self.event_first    = Signal()
        self.general_first  = Signal()
        self.event_msg_type = Signal(4)

        # # #

        event_in_pkt   = Signal()
        general_in_pkt = Signal()

        # Event Port.
        # -----------
        self.sync += [
            self.event_first.eq(0),
            If(event_port.source.valid & event_port.source.ready,
                If(~event_in_pkt,
                    self.event_first.eq(1),
                    self.event_msg_type.eq(event_port.source.data[0:4]),
                    event_in_pkt.eq(1)
                ),
                If(event_port.source.last,
                    event_in_pkt.eq(0)
                )
            )
        ]

        # General Port.
        # -------------
        self.sync += [
            self.general_first.eq(0),
            If(general_port.source.valid & general_port.source.ready,
                If(~general_in_pkt,
                    self.general_first.eq(1),
                    general_in_pkt.eq(1)
                ),
                If(general_port.source.last,
                    general_in_pkt.eq(0)
                )
            )
        ]
