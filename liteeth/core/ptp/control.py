#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


from migen import *

from litex.gen import LiteXModule

from liteeth.core.ptp.common import *


class LiteEthPTPControl(LiteXModule):
    """
    PTP Protocol FSM.

    Manages the E2E/P2P exchange sequence: WAIT_SYNC → WAIT_FUP → SEND_DELAY_REQ →
    WAIT_DELAY_RESP → SERVE → LOCKED, and drives TX, Servo, and timestamp latching.
    """
    def __init__(self, tsu, tx, rx_ev, rx_ge, servo, latcher, event_source, general_source,
        enable=Signal(reset=1), require_announce=Signal(reset=0), announce_timeout_cycles=None):
        # Control/Status.
        # ---------------
        self.locked             = Signal() # o

        # Master IP Storage.
        # ------------------
        self.master_ip = Signal(32) # o

        # # #

        # Signals.
        # --------
        seq                   = Signal(16) # PTP Sequence ID.
        rx_ts_shadow          = Signal(80)
        tx_ts_shadow          = Signal(80)
        rx_ts_shadow_valid    = Signal()
        tx_ts_shadow_valid    = Signal()
        skip_stale_sync       = Signal()
        rx_ts_capture_pending = Signal()
        tx_ts_capture_pending = Signal()
        t1                    = Signal(80)
        t2                    = Signal(80)
        t3                    = Signal(80)
        t4                    = Signal(80)
        p1                    = Signal(80)
        p2                    = Signal(80)
        p3                    = Signal(80)
        p4                    = Signal(80)
        have_t1               = Signal()
        have_t2               = Signal()
        have_t3               = Signal()
        have_t4               = Signal()
        master_known          = Signal()
        announce_seen         = Signal()
        announce_expired      = Signal()
        event_from_master     = Signal()
        general_from_master   = Signal()
        event_msg_type        = Signal(4)
        event_is_sync         = Signal()
        event_is_pdelay_resp  = Signal()
        event_is_latchable    = Signal()

        if hasattr(latcher, "event_msg_type"):
            self.comb += event_msg_type.eq(latcher.event_msg_type)
            self.comb += [
                event_is_sync.eq(event_msg_type == PTP_MSG_SYNC),
                event_is_pdelay_resp.eq(event_msg_type == PTP_MSG_PDELAY_RESP),
            ]
        else:
            self.comb += [
                event_is_sync.eq(1),
                event_is_pdelay_resp.eq(1),
            ]

        self.comb += [
            master_known.eq(self.master_ip != 0),
            event_from_master.eq(event_source.ip_address == self.master_ip),
            general_from_master.eq(general_source.ip_address == self.master_ip),
            event_is_latchable.eq(event_is_sync | (tx.p2p_mode & event_is_pdelay_resp)),
        ]

        # FSM.
        # ----
        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += fsm.reset.eq((~enable) | announce_expired)
        self.comb += announce_seen.eq(
            fsm.ongoing("WAIT_SYNC") &
            rx_ge.present &
            (rx_ge.msg_type == PTP_MSG_ANNOUNCE)
        )

        # FSM -> TX.
        self.comb += [
            tx.start.eq(0),
            tx.seq_id.eq(seq),
        ]


        # IDLE: wait for enable, clear all state.
        fsm.act("IDLE",
            If(enable,
                NextValue(self.locked, 0),
                NextValue(have_t1, 0),
                NextValue(have_t2, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_SYNC")
            )
        )

        # WAIT_SYNC: accept Announce (learn master IP), then wait for Sync.
        # If a Sync arrives from the master, latch t2 (RX timestamp) and
        # proceed to Follow_Up (two-step) or Delay_Req (one-step).
        fsm.act("WAIT_SYNC",
            If(rx_ge.present & (rx_ge.msg_type == PTP_MSG_ANNOUNCE),
                If((~master_known) | (~general_from_master),
                    NextValue(self.locked, 0)
                ),
                NextValue(self.master_ip, general_source.ip_address)
            ),
            # Wait for SYNC message on Event port.
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               (Mux(require_announce,
                    master_known & event_from_master,
                    (~master_known) | event_from_master)),
                If(skip_stale_sync,
                    # Skip this Sync — it was queued during the previous exchange.
                    NextValue(skip_stale_sync, 0)
                ).Else(
                    NextValue(seq, rx_ev.seq_id),
                    If(rx_ts_shadow_valid,
                        NextValue(t2, rx_ts_shadow),
                        NextValue(have_t2, 1)
                    ).Else(
                        NextValue(have_t2, 0)
                    ),
                    NextValue(rx_ts_shadow_valid, 0),
                    NextValue(have_t3, 0),
                    NextValue(have_t4, 0),
                    If(rx_ev.two_step,
                        NextValue(have_t1, 0),
                        NextState("WAIT_FUP")
                    ).Else(
                        NextValue(t1, rx_ev.timestamp),
                        NextValue(have_t1, 1),
                        If(tx.p2p_mode,
                            NextState("SEND_PDELAY_REQ")
                        ).Else(
                            NextState("SEND_DELAY_REQ")
                        )
                    )
                )
            ),
        )

        # WAIT_FUP: wait for Follow_Up to get t1 (master origin timestamp).
        fsm.act("WAIT_FUP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            # Wait for FOLLOW_UP message on General port (matching sequence ID).
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_FOLLOW_UP) &
               (rx_ge.seq_id  == seq) &
               general_from_master,
                NextValue(t1, rx_ge.timestamp),
                NextValue(have_t1, 1),
                If(tx.p2p_mode,
                    NextState("SEND_PDELAY_REQ")
                ).Else(
                    NextState("SEND_DELAY_REQ")
                )
            )
        )

        # E2E path: Delay_Req / Delay_Resp.
        fsm.act("SEND_DELAY_REQ",
            # Send Delay_Req. TX module sets PTP_MSG_DELAY_REQ.
            tx.start.eq(1),
            If(tx.done,
                If(tx_ts_shadow_valid,
                    NextValue(t3, tx_ts_shadow),
                    NextValue(have_t3, 1)
                ).Else(
                    NextValue(have_t3, 0)
                ),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_DELAY_RESP")
            )
        )
        # WAIT_DELAY_RESP: wait for Delay_Resp from master to get t4.
        # Also accept a new Sync if one arrives (restart the exchange).
        fsm.act("WAIT_DELAY_RESP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            # Wait for Delay_Resp on General port (matching sequence ID).
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_DELAY_RESP) &
               (rx_ge.seq_id  == seq) &
               general_from_master &
               (rx_ge.requesting_port_id == tx.clock_id),
                NextValue(t4, rx_ge.timestamp),
                NextValue(have_t4, 1),
                NextState("SERVE")
            )
        )

        # P2P path: Pdelay_Req / Pdelay_Resp.
        fsm.act("SEND_PDELAY_REQ",
            # Send Pdelay_Req. TX module sets PTP_MSG_PDELAY_REQ.
            tx.start.eq(1),
            If(tx.done,
                If(tx_ts_shadow_valid,
                    NextValue(p1, tx_ts_shadow)
                ),
                NextValue(tx_ts_shadow_valid, 0),
                NextState("WAIT_PDELAY_RESP")
            )
        )
        # WAIT_PDELAY_RESP: wait for Pdelay_Resp to get p2 and p4.
        fsm.act("WAIT_PDELAY_RESP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            # Wait for Pdelay_Resp on Event port (matching sequence ID).
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_PDELAY_RESP) &
               (rx_ev.seq_id  == seq) &
               event_from_master &
               (rx_ev.requesting_port_id == tx.clock_id),
                NextValue(p2, rx_ev.timestamp),
                If(rx_ts_shadow_valid,
                    NextValue(p4, rx_ts_shadow)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                If(rx_ev.two_step,
                    NextState("WAIT_PDELAY_RESP_FUP")
                ).Else(
                    NextValue(p3, rx_ev.timestamp),
                    NextState("SERVE")
                )
            )
        )
        # WAIT_PDELAY_RESP_FUP: wait for Pdelay_Resp_Follow_Up to get p3.
        fsm.act("WAIT_PDELAY_RESP_FUP",
            If(rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               event_from_master,
                NextValue(seq, rx_ev.seq_id),
                If(rx_ts_shadow_valid,
                    NextValue(t2, rx_ts_shadow),
                    NextValue(have_t2, 1)
                ).Else(
                    NextValue(have_t2, 0)
                ),
                NextValue(rx_ts_shadow_valid, 0),
                NextValue(have_t3, 0),
                NextValue(have_t4, 0),
                If(rx_ev.two_step,
                    NextValue(have_t1, 0),
                    NextState("WAIT_FUP")
                ).Else(
                    NextValue(t1, rx_ev.timestamp),
                    NextValue(have_t1, 1),
                    If(tx.p2p_mode,
                        NextState("SEND_PDELAY_REQ")
                    ).Else(
                        NextState("SEND_DELAY_REQ")
                    )
                )
            ),
            If(rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_PDELAY_RESP_FUP) &
               (rx_ge.seq_id  == seq) &
               general_from_master,
                NextValue(p3, rx_ge.timestamp),
                NextState("SERVE")
            )
        )

        # SERVE: trigger servo pipeline. Wait for all 4 timestamps in E2E.
        fsm.act("SERVE",
            If(tx.p2p_mode | (have_t1 & have_t2 & have_t3 & have_t4),
                NextState("LOCKED")
            ).Else(
                NextValue(self.locked, 0),
                NextState("WAIT_SYNC")
            )
        )

        # LOCKED: mark locked, skip stale Sync if last exchange was good
        # (a Sync may have been queued in the depacketizer during SERVE).
        fsm.act("LOCKED",
            NextValue(skip_stale_sync, servo.sample_valid),
            NextValue(rx_ts_shadow_valid, 0),
            NextValue(rx_ts_capture_pending, 0),
            NextValue(self.locked, 1),
            NextState("WAIT_SYNC")
        )


        # FSM -> Servo.
        # -------------
        # Connect timestamp registers and trigger servo pipeline one cycle
        # before LOCKED (fsm.before_entering fires in the SERVE state).
        self.comb += [
            servo.t1.eq(t1),
            servo.t2.eq(t2),
            servo.t3.eq(t3),
            servo.t4.eq(t4),
            servo.p1.eq(p1),
            servo.p2.eq(p2),
            servo.p3.eq(p3),
            servo.p4.eq(p4),
            servo.p2p_mode.eq(tx.p2p_mode),
            servo.serve.eq(fsm.before_entering("LOCKED")),
        ]

        # TSU Latch Drivers.
        # ------------------
        # The TSU's TX and RX latches are driven by the FSM's state and the latcher outputs.
        self.comb += [
            tsu.tx_latch.eq(tx.launch),
            tsu.rx_latch.eq(
                (fsm.ongoing("WAIT_SYNC")            |
                 fsm.ongoing("WAIT_FUP")             |
                 fsm.ongoing("WAIT_DELAY_RESP")      |
                 fsm.ongoing("WAIT_PDELAY_RESP")     |
                 fsm.ongoing("WAIT_PDELAY_RESP_FUP")) &
                latcher.event_first &
                event_is_latchable
            ),
        ]

        if hasattr(tsu, "seconds") and hasattr(tsu, "nanoseconds"):
            self.sync += [
                If(tsu.rx_latch,
                    rx_ts_capture_pending.eq(1)
                ).Elif(rx_ts_capture_pending,
                    rx_ts_shadow.eq(tsu.rx_ts),
                    rx_ts_shadow_valid.eq(1),
                    rx_ts_capture_pending.eq(0)
                ),
                If(tsu.tx_latch,
                    tx_ts_capture_pending.eq(1)
                ).Elif(tx_ts_capture_pending,
                    tx_ts_shadow.eq(tsu.tx_ts),
                    tx_ts_shadow_valid.eq(1),
                    tx_ts_capture_pending.eq(0)
                ),
            ]
        else:
            self.comb += [
                rx_ts_shadow.eq(tsu.rx_ts),
                tx_ts_shadow.eq(tsu.tx_ts),
                rx_ts_shadow_valid.eq(1),
                tx_ts_shadow_valid.eq(1),
            ]

        # Master IP Latching.
        # -------------------
        self.sync += [
            If(announce_expired,
                self.master_ip.eq(0),
                self.locked.eq(0)
            ),
            If(fsm.ongoing("WAIT_SYNC") &
               rx_ev.present &
               (rx_ev.msg_type == PTP_MSG_SYNC) &
               (Mux(require_announce,
                    master_known & event_from_master,
                    (~master_known) | event_from_master)),
                # SYNC is on the Event Port
                self.master_ip.eq(event_source.ip_address)
            ).Elif(fsm.ongoing("WAIT_SYNC") &
               rx_ge.present &
               (rx_ge.msg_type == PTP_MSG_ANNOUNCE),
                self.master_ip.eq(general_source.ip_address)
            ).Elif(fsm.ongoing("WAIT_FUP") & rx_ge.present & (rx_ge.msg_type == PTP_MSG_FOLLOW_UP),
                # FOLLOW_UP is on the General Port
                self.master_ip.eq(general_source.ip_address)
            )
        ]

        if announce_timeout_cycles is not None:
            announce_counter = Signal(max=max(2, announce_timeout_cycles + 1))
            self.sync += [
                announce_expired.eq(0),
                If((~enable) | announce_expired,
                    announce_counter.eq(0)
                ).Elif(announce_seen,
                    announce_counter.eq(announce_timeout_cycles)
                ).Elif(master_known & (announce_counter != 0),
                    announce_counter.eq(announce_counter - 1),
                    If(announce_counter == 1,
                        announce_expired.eq(1)
                    )
                )
            ]
        else:
            self.sync += [
                announce_expired.eq(0),
            ]
