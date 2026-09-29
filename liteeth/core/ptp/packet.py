#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


from migen import *

from litex.gen import LiteXModule

from litex.gen.genlib.misc import WaitTimer
from litex.soc.interconnect.packet import Depacketizer, Packetizer

from liteeth.common import *
from liteeth.core.ptp.common import *


class LiteEthPTPTX(LiteXModule):
    """
    PTP TX (Delay_Req / Pdelay_Req Transmitter).

    Packetizes PTP header + 10-byte originTimestamp body. Asserts ``launch`` on
    the first accepted payload byte to trigger the TSU TX latch.
    """
    def __init__(self, tsu):
        # Control/Status.
        # ---------------
        self.start      = Signal()
        self.done       = Signal()
        self.launch     = Signal()

        # Parameters.
        # -----------
        self.seq_id     = Signal(16)
        self.domain     = Signal(8)
        self.clock_id   = Signal(80)
        self.msg_type   = Signal(4)
        self.ip_address = Signal(32)
        self.src_port   = Signal(16)
        self.dst_port   = Signal(16)
        self.p2p_mode   = Signal()

        # External UDP source (connect to UDP crossbar).
        # ----------------------------------------------
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # Packetizer.
        # -----------
        self.packetizer = packetizer = Packetizer(
            ptp_description(8),
            eth_udp_user_description(8),
            ptp_header
        )

        # # #

        # Signals.
        # --------
        pre_tx_ts  = Signal(80)
        self.count = count = Signal(4)
        ts_byte    = Signal(8)
        sec        = Signal(48)
        ns         = Signal(32)

        # Timestamp Formatting (Big-Endian).
        # ----------------------------------
        self.comb += [
            ns.eq( pre_tx_ts[ 0:32]),
            sec.eq(pre_tx_ts[32:80]),
        ]
        self.comb += Case(count, {
            0: ts_byte.eq(sec[40:48]),
            1: ts_byte.eq(sec[32:40]),
            2: ts_byte.eq(sec[24:32]),
            3: ts_byte.eq(sec[16:24]),
            4: ts_byte.eq(sec[ 8:16]),
            5: ts_byte.eq(sec[ 0: 8]),
            6: ts_byte.eq( ns[24:32]),
            7: ts_byte.eq( ns[16:24]),
            8: ts_byte.eq( ns[ 8:16]),
            9: ts_byte.eq( ns[ 0: 8]),
        })

        # Header.
        # -------
        self.comb += [
            # Fixed/Calculated Fields.
            packetizer.sink.transport_spec.eq(0),
            packetizer.sink.version.eq(PTP_VERSION),
            packetizer.sink.reserved0.eq(0),
            packetizer.sink.length.eq(PTP_HEADER_LENGTH + 10),
            packetizer.sink.reserved1.eq(0),
            packetizer.sink.flags.eq(0),
            packetizer.sink.correction.eq(0),
            packetizer.sink.reserved2.eq(0),
            packetizer.sink.control_field.eq(0x01),
            packetizer.sink.log_interval.eq(0),

            # Dynamic Parameters.
            packetizer.sink.msg_type.eq(self.msg_type),
            packetizer.sink.domain_number.eq(self.domain),
            packetizer.sink.source_port_id.eq(self.clock_id),
            packetizer.sink.sequence_id.eq(self.seq_id),

            # Payload Control.
            packetizer.sink.be.eq(1),
            packetizer.sink.error.eq(0),
        ]

        # Pipeline.
        # ---------
        self.comb += packetizer.source.connect(source)

        # UDP Metadata.
        # -------------
        self.comb += [
            source.src_port.eq(self.src_port),
            source.dst_port.eq(self.dst_port),
            source.ip_address.eq(self.ip_address),
            source.length.eq(PTP_HEADER_LENGTH + 10),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self.done.eq(1),
            If(self.start,
                self.done.eq(0),
                NextValue(pre_tx_ts, Cat(tsu.nanoseconds, tsu.seconds)),
                NextValue(count, 0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.sink.valid.eq(1),
            packetizer.sink.data.eq(ts_byte),
            packetizer.sink.first.eq(count == 0),
            packetizer.sink.last.eq( count == 9),
            If(packetizer.sink.ready,
                NextValue(count, count + 1),
                self.launch.eq(packetizer.sink.first),
                If(packetizer.sink.last,
                    self.done.eq(1),
                    NextState("IDLE")
                )
            )
        )



class LiteEthPTPRX(LiteXModule):
    """
    PTP RX (Depacketizer + Timestamp/Identity Extraction).

    Depacketizes UDP payload into PTP header + body. Extracts 10-byte timestamp and
    10-byte requestingPortIdentity. Uses WaitTimer + ResetInserter for deadlock protection.
    """
    TIMEOUT_CYCLES = 1024

    def __init__(self, udp_port, sys_clk_freq):
        # Control/Status.
        # ---------------
        self.present            = Signal(reset=0)
        self.error              = Signal()
        self.msg_type           = Signal(4)
        self.flags              = Signal(16)
        self.seq_id             = Signal(16)
        self.two_step           = Signal()
        self.timestamp          = Signal(80)
        self.requesting_port_id = Signal(80)
        self.domain             = Signal(8)
        self.invalid_header     = Signal()
        self.timeout_error      = Signal()

        # Depacketizer (with ResetInserter for timeout recovery).
        # -------------------------------------------------------
        rx_fsm_reset      = Signal()
        self.depacketizer = depacketizer = ResetInserter()(Depacketizer(
            eth_udp_user_description(8),
            ptp_description(8),
            ptp_header
        ))
        self.comb += depacketizer.reset.eq(rx_fsm_reset)
        self.sink = depacketizer.sink

        # # #

        # Signals.
        # --------
        bcount         = Signal(5)
        buf            = Array(Signal(8) for _ in range(20))
        version_ok     = Signal()
        domain_ok      = Signal()
        ts_type_raw    = Signal()
        supported_type = Signal()

        # Header Extraction.
        # ------------------
        self.comb += [
            self.msg_type.eq(depacketizer.source.msg_type),
            self.flags.eq(depacketizer.source.flags),
            self.seq_id.eq(depacketizer.source.sequence_id),
            self.two_step.eq(depacketizer.source.flags[PTP_TWO_STEP_FLAG_BIT]),
        ]

        # Validation.
        # -----------
        self.comb += [
            version_ok.eq(depacketizer.source.version     == PTP_VERSION),
            domain_ok.eq(depacketizer.source.domain_number == self.domain),
            ts_type_raw.eq(
                (self.msg_type == PTP_MSG_SYNC)          |
                (self.msg_type == PTP_MSG_FOLLOW_UP)     |
                (self.msg_type == PTP_MSG_DELAY_RESP)    |
                (self.msg_type == PTP_MSG_PDELAY_RESP)   |
                (self.msg_type == PTP_MSG_PDELAY_RESP_FUP)
            ),
            supported_type.eq(
                ts_type_raw |
                (self.msg_type == PTP_MSG_ANNOUNCE)
            )
        ]

        # Timeout.
        # --------
        self.timeout_timer = timeout_timer = WaitTimer(self.TIMEOUT_CYCLES)
        self.comb += rx_fsm_reset.eq(timeout_timer.done)

        # FSM.
        # ----
        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += [
            fsm.reset.eq(rx_fsm_reset),
            timeout_timer.wait.eq(~self.fsm.ongoing("IDLE")),
        ]

        fsm.act("IDLE",
            depacketizer.source.ready.eq(1),
            NextValue(self.present, 0),
            If(depacketizer.source.valid,
                If(version_ok & domain_ok & supported_type,
                    NextValue(buf[0], depacketizer.source.data),
                    If(depacketizer.source.last,
                        NextValue(bcount, 0),
                        NextState("END")
                    ).Else(
                        NextValue(bcount, 1),
                        NextState("BODY")
                    )
                ).Else(
                    NextState("SKIP")
                )
            )
        )

        fsm.act("BODY",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                If(bcount < 20,
                    NextValue(buf[bcount], depacketizer.source.data)
                ),
                If(depacketizer.source.last,
                    NextState("END")
                ).Else(
                    NextValue(bcount, bcount + 1)
                )
            )
        )

        fsm.act("SKIP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid & depacketizer.source.last,
                NextState("END")
            )
        )

        fsm.act("END",
            If(version_ok & domain_ok & supported_type,
                NextValue(self.timestamp, Cat(
                    buf[9], buf[8], buf[7], buf[6],
                    buf[5], buf[4], buf[3], buf[2], buf[1], buf[0]
                )),
                NextValue(self.requesting_port_id, Cat(
                    buf[19], buf[18], buf[17], buf[16], buf[15],
                    buf[14], buf[13], buf[12], buf[11], buf[10]
                )),
            ),
            NextState("DONE")
        )

        fsm.act("DONE",
            NextValue(self.present, version_ok & domain_ok & supported_type),
            NextState("IDLE")
        )

        # Error Reporting.
        # ----------------
        self.sync += [
            self.invalid_header.eq(0),
            self.timeout_error.eq(0),
            self.error.eq(0),
            If(self.fsm.ongoing("IDLE") & depacketizer.source.valid & ~(version_ok & domain_ok),
                self.invalid_header.eq(1),
            ),
            If(rx_fsm_reset,
                self.timeout_error.eq(1),
                self.error.eq(1),
            ),
        ]
