#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


"""
LiteEth PTP (IEEE 1588v2) Slave Core — Layer 3 (UDP/IPv4) only.

Provides a PTP Slave implementation for LiteEth-based systems with TSU, Clock Servo,
and Protocol FSM. Currently supports Slave mode only (no Master/Boundary
Clock) and Layer 3 transport (UDP over IPv4) only (no Layer 2/Ethernet transport).

Features:
- End-To-End (E2E) and Peer-To-Peer (P2P) delay mechanisms.
- 48-bit seconds / 32-bit nanoseconds TSU with addend-based tick accumulation.
- Pipelined clock servo with phase correction and frequency trim.
- Outlier detection and seconds-boundary correction.
- Minimal CSRs for lock/master status.
"""

from migen import *

from litex.gen import LiteXModule

from litex.gen.genlib.misc import WaitTimer
from litex.soc.interconnect.csr import CSRStatus

from liteeth.core.ptp.common import *
from liteeth.core.ptp.clock import LiteEthTSU, LiteEthPTPRxTimestamp
from liteeth.core.ptp.packet import LiteEthPTPTX, LiteEthPTPRX
from liteeth.core.ptp.servo import LiteEthPTPClockServo
from liteeth.core.ptp.control import LiteEthPTPControl


class LiteEthPTP(LiteXModule):
    """
    PTP Top-Level Module.

    Integrates TSU, TX/RX, Protocol FSM, and Clock Servo to provide a
    complete PTP slave implementation. Supports E2E and P2P delay mechanisms.

    Parameters:
    - event_port    : UDP port for PTP event messages (port 319).
    - general_port  : UDP port for PTP general messages (port 320).
    - sys_clk_freq  : System clock frequency.
    - timeout       : Lock timeout in seconds.
    """
    def __init__(self, event_port, general_port, sys_clk_freq, timeout=1.0, announce_timeout=None,
        require_announce=False, unicast_delay_req=False):
        # Control/Status.
        # ---------------
        self.enable                = Signal(reset=1)
        self.locked                = Signal()
        self.timeout               = Signal()
        self.p2p_mode              = Signal(reset=0)
        self.unicast_delay_req     = Signal(reset=1 if unicast_delay_req else 0)
        self.require_announce      = Signal(reset=1 if require_announce else 0)
        self.master_ip             = Signal(32)

        # Parameters.
        # -----------
        self.clock_id = Signal(80, reset=0x0000000000000001)
        self.domain   = Signal(8,  reset=0)

        # # #

        # 1. Time-Stamping Unit (TSU).
        self.tsu = tsu = LiteEthTSU(sys_clk_freq)

        # 2. TX/RX Helpers.
        self.tx         = tx    = LiteEthPTPTX(tsu)
        self.rx_event   = rx_ev = LiteEthPTPRX(PTP_EVENT_PORT, sys_clk_freq=sys_clk_freq)
        self.rx_general = rx_ge = LiteEthPTPRX(PTP_GENERAL_PORT, sys_clk_freq=sys_clk_freq)

        # 3. Timestamp Latching.
        self.latcher = latcher = LiteEthPTPRxTimestamp(event_port, general_port)

        # 4. Clock Servo.
        self.servo = servo = LiteEthPTPClockServo(tsu)

        # 5. Protocol Control (FSM).
        self.control = control = LiteEthPTPControl(
            tsu, tx, rx_ev, rx_ge, servo, latcher,
            event_source            = event_port.source,
            general_source          = general_port.source,
            enable                  = self.enable,
            require_announce        = self.require_announce,
            announce_timeout_cycles = (
                None if announce_timeout is None else int(announce_timeout*sys_clk_freq)
            )
        )

        # I/O Wiring.
        # -----------
        self.comb += [
            # RX.
            event_port.source.connect(rx_ev.sink),
            general_port.source.connect(rx_ge.sink),
            rx_ev.domain.eq(self.domain),
            rx_ge.domain.eq(self.domain),

            # TX.
            tx.source.connect(event_port.sink),
            tx.domain.eq(self.domain),
            tx.clock_id.eq(self.clock_id),

            tx.src_port.eq(PTP_EVENT_PORT),
            tx.dst_port.eq(PTP_EVENT_PORT),
            # Propagate P2P mode to TX helper and select message type.
            tx.p2p_mode.eq(self.p2p_mode),
            If(self.p2p_mode,
                tx.msg_type.eq(PTP_MSG_PDELAY_REQ),
                tx.ip_address.eq(PTP_PDELAY_MCAST_IP),
            ).Elif(self.unicast_delay_req,
                tx.msg_type.eq(PTP_MSG_DELAY_REQ),
                tx.ip_address.eq(control.master_ip),
            ).Else(
                tx.msg_type.eq(PTP_MSG_DELAY_REQ),
                tx.ip_address.eq(PTP_PRIMARY_MCAST_IP),
            )
        ]

        # Top-level status outputs.
        self.comb += [
            self.locked.eq(control.locked),
            self.master_ip.eq(control.master_ip),
        ]

        # CSRs.
        # -----
        self._locked                = CSRStatus(description="PTP lock status.")
        self._master_ip             = CSRStatus(32, description="Master IPv4.")
        self.comb += [
            self._locked.status.eq(self.locked),
            self._master_ip.status.eq(self.master_ip),
        ]

        # 6. Timeout Timer.
        self.timeout_timer = timeout_timer = WaitTimer(int(timeout*sys_clk_freq))
        self.comb += [
            timeout_timer.wait.eq(
                self.enable &
                (self.locked == 0) &
                ( ~timeout_timer.done )
            ),
            self.timeout.eq(timeout_timer.done),
        ]
