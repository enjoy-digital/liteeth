#
# This file is part of LiteEth.
#
# Copyright (c) 2024-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause


from litex.soc.interconnect.packet import Header, HeaderField
from litex.soc.interconnect.stream import EndpointDescription

# PTP Constants ------------------------------------------------------------------------------------

PTP_EVENT_PORT          = 319
PTP_GENERAL_PORT        = 320

PTP_PRIMARY_MCAST_IP    = 0xE0000181 # 224.0.1.129.
PTP_PDELAY_MCAST_IP     = 0xE000006B # 224.0.0.107.

PTP_HEADER_LENGTH       = 34 # Bytes.

# Message types (low nibble).
PTP_MSG_SYNC            = 0x0  # Event
PTP_MSG_DELAY_REQ       = 0x1  # Event
PTP_MSG_PDELAY_REQ      = 0x2  # Event (P2P)
PTP_MSG_PDELAY_RESP     = 0x3  # Event (P2P)

PTP_MSG_FOLLOW_UP       = 0x8  # General
PTP_MSG_DELAY_RESP      = 0x9  # General
PTP_MSG_PDELAY_RESP_FUP = 0xA  # General (P2P)
PTP_MSG_ANNOUNCE        = 0xB  # General

PTP_VERSION             = 0x2
PTP_TWO_STEP_FLAG_BIT   = 9

# PTP Header ---------------------------------------------------------------------------------------

ptp_header_fields = {
    "msg_type"       : HeaderField(0,  0,  4),
    "transport_spec" : HeaderField(0,  4,  4),
    "version"        : HeaderField(1,  0,  4),
    "reserved0"      : HeaderField(1,  4,  4),
    "length"         : HeaderField(2,  0, 16),
    "domain_number"  : HeaderField(4,  0,  8),
    "reserved1"      : HeaderField(5,  0,  8),
    "flags"          : HeaderField(6,  0, 16),
    "correction"     : HeaderField(8,  0, 64),
    "reserved2"      : HeaderField(16, 0, 32),
    "source_port_id" : HeaderField(20, 0, 80),
    "sequence_id"    : HeaderField(30, 0, 16),
    "control_field"  : HeaderField(32, 0,  8),
    "log_interval"   : HeaderField(33, 0,  8),
}
ptp_header = Header(ptp_header_fields, PTP_HEADER_LENGTH, swap_field_bytes=True)

# PTP Description ----------------------------------------------------------------------------------


def ptp_description(dw):
    param_layout   = ptp_header.get_layout()
    payload_layout = [
        ("data",    dw),
        ("be",      dw//8),
        ("error",   dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)
