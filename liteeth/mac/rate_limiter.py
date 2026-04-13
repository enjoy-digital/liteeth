#
# This file is part of LiteEth.
#
# Copyright (c) 2026 OpenAI
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from migen.genlib.cdc import MultiReg

from litex.gen.genlib.cdc import BusSynchronizer

from liteeth.common import *


class LiteEthMACTokenBucket(LiteXModule):
    def __init__(self, dw, enable=False, rate=0, burst=None):
        assert dw in [8, 16, 32, 64]

        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        self.enable = Signal()
        self.rate   = Signal(32)
        self.burst  = Signal(32)

        # `eth_mtu` already includes eth_interpacket_gap, so it is the full
        # per-frame charge budget — no extra overhead adjustment needed.
        max_frame_charge = eth_mtu
        if burst is None:
            burst = max_frame_charge

        self._enable = CSRStorage(1,  name="enable", description="Enable MAC TX token-bucket rate limiter.", reset=enable)
        self._rate   = CSRStorage(32, name="rate",   description="Refill rate in Q16.16 bytes per eth_tx cycle.", reset=rate)
        self._burst  = CSRStorage(32, name="burst",  description="Burst budget in bytes.", reset=burst)

        # # #

        byte_width               = dw // 8
        byte_count_bits          = bits_for(max_frame_charge + byte_width)
        token_bits               = 48

        csr_enable = Signal()

        self.specials += MultiReg(self._enable.storage, csr_enable, "eth_tx")

        self.rate_cdc = rate_cdc = BusSynchronizer(32, "sys", "eth_tx")
        self.burst_cdc = burst_cdc = BusSynchronizer(32, "sys", "eth_tx")
        self.comb += [
            rate_cdc.i.eq(self._rate.storage),
            burst_cdc.i.eq(self._burst.storage),
        ]

        # Decode how many bytes are valid on the final beat.
        last_bytes = Signal(max=byte_width + 1)
        last_be_cases = {2**i: last_bytes.eq(i + 1) for i in range(byte_width)}
        last_be_cases["default"] = last_bytes.eq(byte_width)
        self.comb += Case(sink.last_be, last_be_cases)

        # Per-beat tracking.
        beat_bytes          = Signal(max=byte_width + 1)
        current_frame_bytes = Signal(byte_count_bits)
        transfer            = Signal()

        # Token bucket state.
        charge_tokens        = Signal(token_bits)
        effective_burst      = Signal(32)
        effective_burst_tokens = Signal(token_bits)
        frame_can_start      = Signal()

        # Refill pipeline.
        tokens_plus_rate   = Signal(token_bits + 1)
        refill_saturates   = Signal()
        refilled_tokens    = Signal(token_bits)

        byte_count = Signal(byte_count_bits)
        tokens     = Signal(token_bits, reset=(burst << 16))

        self.comb += [
            sink.connect(source, omit={"ready", "valid"}),
            beat_bytes.eq(Mux(sink.last, last_bytes, byte_width)),
            current_frame_bytes.eq(byte_count + beat_bytes),
            charge_tokens.eq(current_frame_bytes << 16),
            # Never allow a burst smaller than one full frame reservation.
            If(self.burst < max_frame_charge,
                effective_burst.eq(max_frame_charge)
            ).Else(
                effective_burst.eq(self.burst)
            ),
            effective_burst_tokens.eq(effective_burst << 16),
            tokens_plus_rate.eq(tokens + self.rate),
            refill_saturates.eq(tokens_plus_rate >= effective_burst_tokens),
            # Saturating refill value used by the explicit synchronous accumulator.
            If(refill_saturates,
                refilled_tokens.eq(effective_burst_tokens)
            ).Else(
                refilled_tokens.eq(tokens_plus_rate[:token_bits])
            ),
            # Admission is frame-gated: a frame starts only with a worst-case budget.
            frame_can_start.eq((tokens >= (max_frame_charge << 16)) | ~self.enable),
            transfer.eq(source.valid & source.ready),
        ]

        self.fsm = fsm = ClockDomainsRenamer("eth_tx")(FSM(reset_state="IDLE"))
        fsm.act("IDLE",
            # Hold the first beat until the bucket can cover any legal frame.
            source.valid.eq(sink.valid & frame_can_start),
            sink.ready.eq(source.ready & frame_can_start),
            If(transfer & ~source.last,
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            # Once a frame starts, pass it through without inserting bubbles.
            source.valid.eq(sink.valid),
            sink.ready.eq(source.ready),
            If(transfer & source.last,
                NextState("IDLE")
            )
        )

        self.sync.eth_tx += [
            If(fsm.ongoing("IDLE"),
                # Commit CSR updates only between frames.
                self.enable.eq(csr_enable),
                self.rate.eq(rate_cdc.o),
                self.burst.eq(burst_cdc.o),
            ),
            # Refill: explicit per-cycle accumulation with burst saturation.
            # This is the default token update — every cycle adds `rate`.
            tokens.eq(refilled_tokens),
            # Charge: deduct on last beat (overrides refill via last-writer-wins).
            If(self.enable & transfer & source.last,
                tokens.eq(refilled_tokens - charge_tokens),
            ),
            # Bypass: when disabled, keep bucket full (overrides all above).
            If(~self.enable,
                tokens.eq(effective_burst_tokens),
            ),
            If(transfer,
                If(source.last,
                    byte_count.eq(0)
                ).Else(
                    byte_count.eq(current_frame_bytes)
                )
            )
        ]
