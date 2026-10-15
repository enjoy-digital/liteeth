#
# This file is part of LiteEth.
#
# Copyright (c) 2021-2025 Charles-Henri Mousset <ch.mousset@gmail.com>
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""10BASE-T PHY using FPGA IOs directly on the twisted pairs.

Based on https://www.fpga4fun.com/10BASE-T.html: the transmit pair is driven by two FPGA outputs and
the receive pair is sampled by a differential input, with only a few resistors and capacitors (or
transformers) between the FPGA and the RJ45. See bench/arty_10baset.py for a wiring example.

This is not an IEEE 802.3 compliant MAU (no isolation, no waveform shaping, no collision detection,
no jabber) and only supports half-duplex operation without collisions, which is what a link partner
selects through parallel detection when it only sees Normal Link Pulses. Use at your own risk.
"""

from math import ceil

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.misc import WaitTimer
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DifferentialInput

from liteeth.common import *
from liteeth.phy.common import *

# 10BASE-T Constants -------------------------------------------------------------------------------

baset_bit_time       = 100e-9 # 10Mb/s.
baset_tp_idl_time    = 300e-9 # Start of TP_IDL: high for at least 250ns after the last bit.
baset_ifg_time       = 9.6e-6 # 96 bit times between frames, TP_IDL included.
baset_nlp_time       = 100e-9 # Normal Link Pulse width.
baset_nlp_period     = 16e-3  # Normal Link Pulse period when idle (16ms +/- 8ms).
baset_link_loss_time = 150e-3 # Link fails without a frame/link pulse received in this time (50-150ms).

# 10BASE-T TX --------------------------------------------------------------------------------------

class LiteEthPHY10BASETTX(LiteXModule):
    """Manchester encoder, TP_IDL, inter-frame gap and Normal Link Pulses.

    Frames from the MAC (preamble/SFD included) are sent LSB first. The MAC gap inserter counts
    clock cycles, not bytes, so the inter-frame gap is enforced here.
    """
    def __init__(self, clk_freq, nlp_period=baset_nlp_period):
        self.sink = sink = stream.Endpoint(eth_phy_description(8))
        self.o    = Signal() # Line level (1: positive).
        self.oe   = Signal() # Line driven.

        # # #

        half_bit_cycles = clk_freq/20e6 # 20MHz half-bit rate.
        assert half_bit_cycles == int(half_bit_cycles) and half_bit_cycles >= 2, \
            "10BASE-T TX clock must be a multiple of 20MHz."
        half_bit_cycles = int(half_bit_cycles)
        tp_idl_ticks    = round(baset_tp_idl_time/(baset_bit_time/2))
        ifg_ticks       = round(baset_ifg_time/(baset_bit_time/2))
        nlp_ticks       = round(baset_nlp_time/(baset_bit_time/2))
        nlp_period      = round(nlp_period/(baset_bit_time/2))

        # Half-bit tick.
        tick    = Signal()
        tick_cd = Signal(max=half_bit_cycles)
        self.sync += [
            tick_cd.eq(tick_cd - 1),
            If(tick, tick_cd.eq(half_bit_cycles - 1)),
        ]
        self.comb += tick.eq(tick_cd == 0)

        # Signals.
        data     = Signal(8)
        last     = Signal()
        bit      = Signal(3)
        phase    = Signal() # 0: first half of the bit, 1: second half.
        underrun = Signal()
        count    = Signal(max=max(ifg_ticks, nlp_period) + 1)

        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(tick,
                NextValue(count, count + 1),
                If(sink.valid,
                    sink.ready.eq(1),
                    NextValue(data,  sink.data),
                    NextValue(last,  sink.last),
                    NextValue(bit,   0),
                    NextValue(phase, 0),
                    NextState("DATA"),
                ).Elif(count == (nlp_period - 1),
                    NextValue(count, 0),
                    NextState("NLP"),
                )
            )
        )
        fsm.act("NLP",
            self.o.eq(1),
            self.oe.eq(1),
            If(tick,
                NextValue(count, count + 1),
                If(count == (nlp_ticks - 1),
                    NextValue(count, 0),
                    NextState("IDLE"),
                )
            )
        )
        # Manchester: a 1 is sent low then high, a 0 high then low.
        fsm.act("DATA",
            self.o.eq(data[0] ^ ~phase),
            self.oe.eq(1),
            If(tick,
                NextValue(phase, ~phase),
                If(phase,
                    NextValue(data, data[1:]),
                    NextValue(bit, bit + 1),
                    If(bit == 7,
                        If(last,
                            NextValue(count, 0),
                            NextState("TP_IDL"),
                        ).Elif(sink.valid,
                            sink.ready.eq(1),
                            NextValue(data, sink.data),
                            NextValue(last, sink.last),
                        ).Else(
                            # Underrun: a PHY can't pause a frame, abort it (the partner sees a bad
                            # FCS) and drop the rest of it.
                            NextValue(underrun, 1),
                            NextValue(count, 0),
                            NextState("TP_IDL"),
                        )
                    )
                )
            )
        )
        fsm.act("TP_IDL",
            self.o.eq(count < tp_idl_ticks),
            self.oe.eq(count < tp_idl_ticks),
            If(underrun,
                sink.ready.eq(1),
                If(sink.valid & sink.last,
                    NextValue(underrun, 0),
                )
            ),
            If(tick,
                NextValue(count, count + 1),
                If((count >= (ifg_ticks - 1)) & ~underrun,
                    # Restart the link pulse period after the frame.
                    NextValue(count, 0),
                    NextState("IDLE"),
                )
            )
        )

# 10BASE-T RX --------------------------------------------------------------------------------------

class LiteEthPHY10BASETRX(LiteXModule):
    """Manchester decoder.

    Each bit has a transition in its middle (rising for a 1, falling for a 0) and may have one at its
    boundary. Transitions closer than 3/4 bit time to the previous mid-bit transition are boundary
    ones and are ignored, so the decoder follows the partner's clock. No mid-bit transition for 1.5
    bit time ends the burst (frame: TP_IDL, link pulse: return to idle).

    The pair polarity is detected on the SFD and corrected. The SFD is the first byte sent to the
    MAC, as expected by its preamble checker.
    """
    def __init__(self, clk_freq):
        self.i          = Signal() # Line level, synchronized.
        self.source     = source = stream.Endpoint(eth_phy_description(8))
        self.link_pulse = Signal() # Pulsed on a link pulse.
        self.frame      = Signal() # Pulsed on a frame start (SFD).
        self.inverted   = Signal() # Receive pair polarity is inverted.

        # # #

        # With +/-1 cycle of sampling uncertainty on each transition, boundary (1/2 bit) and mid-bit
        # (1 bit) transitions can only be told apart from 6 cycles per bit.
        bit_cycles = clk_freq*baset_bit_time
        assert bit_cycles >= 6, "10BASE-T RX clock must be >= 60MHz."
        blank_cycles   = ceil(0.75*bit_cycles)
        timeout_cycles = ceil(1.5*bit_cycles)

        self.comb += source.be.eq(1)

        # Transition detection / bit recovery.
        i_d     = Signal()
        since   = Signal(max=timeout_cycles) # Cycles since the last mid-bit transition, minus 1.
        active  = Signal()
        bit_ce  = Signal()
        bit     = Signal()
        eob     = Signal() # End of burst.
        self.sync += i_d.eq(self.i)
        self.comb += [
            bit_ce.eq((self.i != i_d) & (~active | (since >= (blank_cycles - 1)))),
            bit.eq(self.i), # Level after a mid-bit transition: 1 for rising, 0 for falling.
            eob.eq(active & (since == (timeout_cycles - 1))),
        ]
        self.sync += [
            If(since != (timeout_cycles - 1),
                since.eq(since + 1),
            ),
            If(bit_ce,
                since.eq(0),
                active.eq(1),
            ).Elif(eob,
                active.eq(0),
            ),
        ]

        # Burst/frame decoding.
        sr          = Signal(8)
        sr_next     = Signal(8)
        bits        = Signal(3)
        transitions = Signal(2) # Saturating.
        pending     = Signal(8) # Last complete byte, sent on the next one or with last at the end.
        sfd         = eth_preamble >> 56
        self.comb += sr_next.eq(Cat(sr[1:], bit))

        self.fsm = fsm = FSM(reset_state="SYNC")
        fsm.act("SYNC",
            If(bit_ce,
                NextValue(sr, sr_next),
                If(transitions != 3,
                    NextValue(transitions, transitions + 1),
                ),
                If(sr_next == sfd,
                    NextValue(self.inverted, 0),
                    self.frame.eq(1),
                    NextState("DATA"),
                ).Elif(sr_next == (~sfd & 0xff),
                    NextValue(self.inverted, 1),
                    self.frame.eq(1),
                    NextState("DATA"),
                ),
                NextValue(bits, 0),
                NextValue(pending, sfd),
            ),
            If(eob,
                # A link pulse is a single positive pulse: two transitions, 3/4 to 1.5 bit time
                # apart (100ns nominal), so short glitches are not taken as link pulses.
                self.link_pulse.eq(transitions == 2),
                NextValue(sr, 0),
                NextValue(transitions, 0),
            )
        )
        fsm.act("DATA",
            If(bit_ce,
                NextValue(sr, sr_next),
                NextValue(bits, bits + 1),
                If(bits == 7,
                    source.valid.eq(1),
                    source.data.eq(pending),
                    NextValue(pending, sr_next ^ Replicate(self.inverted, 8)),
                )
            ),
            If(eob,
                # Trailing bits not making a full byte (dribble bits) are dropped.
                source.valid.eq(1),
                source.last.eq(1),
                source.data.eq(pending),
                NextValue(sr, 0),
                NextValue(transitions, 0),
                NextState("SYNC"),
            )
        )

# 10BASE-T Link Monitor ----------------------------------------------------------------------------

class LiteEthPHY10BASETLinkMonitor(LiteXModule):
    """Link up while frames or link pulses are received, down after link_loss_time without."""
    def __init__(self, clk_freq):
        self.activity = Signal() # i.
        self.link_up  = Signal() # o.

        # # #

        timer = WaitTimer(ceil(baset_link_loss_time*clk_freq))
        self.timer = ResetInserter()(timer)
        self.comb += [
            self.timer.reset.eq(self.activity),
            self.timer.wait.eq(1),
        ]
        self.sync += [
            If(self.activity,
                self.link_up.eq(1),
            ).Elif(self.timer.done,
                self.link_up.eq(0),
            )
        ]

# 10BASE-T CRG -------------------------------------------------------------------------------------

class LiteEthPHY10BASETCRG(LiteXModule):
    def __init__(self, refclk_cd, with_hw_init_reset=True):
        self._reset = CSRStorage(description="PHY reset.")

        # # #

        # RX/TX clocks.
        # -------------
        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()
        self.comb += [
            self.cd_eth_rx.clk.eq(ClockSignal(refclk_cd)),
            self.cd_eth_tx.clk.eq(ClockSignal(refclk_cd)),
        ]

        # Reset.
        # ------
        self.reset = reset = Signal()
        if with_hw_init_reset:
            self.hw_reset = LiteEthPHYHWReset()
            self.comb += reset.eq(self._reset.storage | self.hw_reset.reset)
        else:
            self.comb += reset.eq(self._reset.storage)
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset),
            AsyncResetSynchronizer(self.cd_eth_rx, reset),
        ]

# 10BASE-T PHY -------------------------------------------------------------------------------------

class LiteEthPHY10BASET(LiteXModule):
    """10BASE-T PHY on FPGA IOs.

    Parameters:
    - pads: transmit with ``td_p``/``td_n`` (tristated when idle) or ``tx``/``tx_en`` (to an external
      driver); receive with ``rd_p``/``rd_n`` (differential input) or ``rx`` (external comparator).
    - refclk_cd: clock domain the PHY runs from (also used for the MAC's eth_rx/eth_tx domains).
    - clk_freq: refclk_cd frequency, a multiple of 20MHz, >= 60MHz (higher gives more RX margin).
    """
    dw = 8
    def __init__(self, pads, clk_freq, refclk_cd="eth", with_hw_init_reset=True):
        self.tx_clk_freq = clk_freq
        self.rx_clk_freq = clk_freq
        self.link_up     = Signal()
        self._status     = CSRStatus(fields=[
            CSRField("link_up",  size=1, description="Link up (frames/link pulses received)."),
            CSRField("inverted", size=1, description="Receive pair polarity inverted."),
        ])

        # # #

        # CRG.
        # ----
        self.crg = LiteEthPHY10BASETCRG(refclk_cd, with_hw_init_reset)

        # TX.
        # ---
        self.tx = tx = ClockDomainsRenamer("eth_tx")(LiteEthPHY10BASETTX(clk_freq))
        tx_o  = Signal()
        tx_oe = Signal()
        self.sync.eth_tx += [
            tx_o.eq(tx.o),
            tx_oe.eq(tx.oe),
        ]
        if hasattr(pads, "td_p"):
            self.specials += Tristate(pads.td_p,  tx_o, tx_oe)
            self.specials += Tristate(pads.td_n, ~tx_o, tx_oe)
        else:
            self.comb += [
                pads.tx.eq(tx_o),
                pads.tx_en.eq(tx_oe),
            ]

        # RX.
        # ---
        rx = Signal()
        if hasattr(pads, "rd_p"):
            self.specials += DifferentialInput(pads.rd_p, pads.rd_n, rx)
        else:
            self.comb += rx.eq(pads.rx)
        self.rx = ClockDomainsRenamer("eth_rx")(LiteEthPHY10BASETRX(clk_freq))
        self.specials += MultiReg(rx, self.rx.i, odomain="eth_rx")

        # Link.
        # -----
        self.link = ClockDomainsRenamer("eth_rx")(LiteEthPHY10BASETLinkMonitor(clk_freq))
        self.comb += self.link.activity.eq(self.rx.link_pulse | self.rx.frame)
        self.specials += [
            MultiReg(self.link.link_up, self.link_up),
            MultiReg(self.link.link_up, self._status.fields.link_up),
            MultiReg(self.rx.inverted,  self._status.fields.inverted),
        ]

        self.sink, self.source = self.tx.sink, self.rx.source
