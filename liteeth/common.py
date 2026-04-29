#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from math import ceil

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream
from litex.soc.interconnect.stream import EndpointDescription
from litex.soc.interconnect.csr import *

from litex.soc.interconnect.packet import Header, HeaderField

from typing import override

# Ethernet Constants -------------------------------------------------------------------------------

eth_mtu_default      = 1530
eth_mtu_jumboframe   = 9022
eth_min_frame_length = 64
eth_fcs_length       = 4
eth_interpacket_gap  = 12
eth_preamble         = 0xd555555555555555

ethernet_type_ip     = 0x800
ethernet_type_arp    = 0x806

# MAC Constants/Header -----------------------------------------------------------------------------

mac_header_length = 14
mac_header_fields = {
    "target_mac":    HeaderField(0,  0, 48),
    "sender_mac":    HeaderField(6,  0, 48),
    "ethernet_type": HeaderField(12, 0, 16)
}
mac_header = Header(mac_header_fields, mac_header_length, swap_field_bytes=True)

# ARP Constants/Header -----------------------------------------------------------------------------

arp_hwtype_ethernet = 0x0001
arp_proto_ip        = 0x0800
arp_opcode_request  = 0x0001
arp_opcode_reply    = 0x0002
arp_min_length      = eth_min_frame_length - eth_fcs_length - mac_header_length

arp_header_length   = 28
arp_header_fields = {
    "hwtype":     HeaderField(0,  0, 16),
    "proto":      HeaderField(2,  0, 16),
    "hwsize":     HeaderField(4,  0,  8),
    "protosize":  HeaderField(5,  0,  8),
    "opcode":     HeaderField(6,  0, 16),
    "sender_mac": HeaderField(8,  0, 48),
    "sender_ip":  HeaderField(14, 0, 32),
    "target_mac": HeaderField(18, 0, 48),
    "target_ip":  HeaderField(24, 0, 32)
}
arp_header = Header(arp_header_fields, arp_header_length, swap_field_bytes=True)

# Broadcast Constants ------------------------------------------------------------------------------

bcast_ip_mask     = 0xff
bcast_mac_address = 0xffffffffffff

# Multicast Constants ------------------------------------------------------------------------------

mcast_oui     = C(0x01005e, 24)
mcast_ip_mask = 224 >> 4

# IPV4 Constants/Header ----------------------------------------------------------------------------

ipv4_header_length = 20
ipv4_header_fields = {
    "ihl":            HeaderField(0,  0,  4),
    "version":        HeaderField(0,  4,  4),
    "total_length":   HeaderField(2,  0, 16),
    "identification": HeaderField(4,  0, 16),
    "dont_fragment":  HeaderField(6,  6,  1),
    "ttl":            HeaderField(8,  0,  8),
    "protocol":       HeaderField(9,  0,  8),
    "checksum":       HeaderField(10, 0, 16),
    "sender_ip":      HeaderField(12, 0, 32),
    "target_ip":      HeaderField(16, 0, 32)
}
ipv4_header = Header(ipv4_header_fields, ipv4_header_length, swap_field_bytes=True)

# ICMP Constants/Header ----------------------------------------------------------------------------

icmp_protocol          = 0x01
icmp_type_ping_reply   = 0
icmp_type_ping_request = 8
icmp_header_length     = 8
icmp_header_fields     = {
    "msgtype":  HeaderField(0, 0,  8),
    "code":     HeaderField(1, 0,  8),
    "checksum": HeaderField(2, 0, 16),
    "quench":   HeaderField(4, 0, 32)
}
icmp_header  = Header(icmp_header_fields, icmp_header_length, swap_field_bytes=True)

# UDP Constants/Header -----------------------------------------------------------------------------
udp_protocol      = 0x11
udp_header_length = 8
udp_header_fields = {
    "src_port": HeaderField(0, 0, 16),
    "dst_port": HeaderField(2, 0, 16),
    "length":   HeaderField(4, 0, 16),
    "checksum": HeaderField(6, 0, 16)
}
udp_header = Header(udp_header_fields, udp_header_length, swap_field_bytes=True)

# BTH Constants/Header --------------------------------------------------------------------------
bth_header_length = 12
bth_header_fields = {
    "opcode":  HeaderField(0, 0, 8),
    "se":      HeaderField(1, 7, 1),
    "m":       HeaderField(1, 6, 1),
    "pad":     HeaderField(1, 4, 2),
    "tver":    HeaderField(1, 0, 4),
    "p_key":   HeaderField(2, 0, 16),
    "dest_qp": HeaderField(5, 0, 24),
    "a":       HeaderField(8, 7, 1),
    "psn":     HeaderField(9, 0, 24)
}
bth_header = Header(bth_header_fields, bth_header_length, swap_field_bytes=True)

reth_header_length = 16
reth_header_fields = {
    "va":      HeaderField(0,  0, 64),
    "r_key":   HeaderField(8,  0, 32),
    "dma_len": HeaderField(12, 0, 32),
}
reth_header = Header(reth_header_fields, reth_header_length, swap_field_bytes=True)

atomiceth_header_length = 28
atomiceth_header_fields = {
    "va":       HeaderField(0,  0, 64),
    "r_key":    HeaderField(8,  0, 32),
    "sa_data":  HeaderField(12, 0, 64),
    "cmp_data": HeaderField(20, 0, 64)
}
atomiceth_header = Header(atomiceth_header_fields, atomiceth_header_length, swap_field_bytes=True)

aeth_header_length = 4
aeth_header_fields = {
    "syndrome": HeaderField(0, 0, 8),
    "msn":      HeaderField(1, 0, 24),
}
aeth_header = Header(aeth_header_fields, aeth_header_length, swap_field_bytes=True)

atomicacketh_header_length = 8
atomicacketh_header_fields = {
    "or_rem_data": HeaderField(0, 0, 64),
}
atomicacketh_header = Header(atomicacketh_header_fields, atomicacketh_header_length, swap_field_bytes=True)

deth_header_length = 8
deth_header_fields = {
    "q_key":  HeaderField(0, 0, 32),
    "src_qp": HeaderField(5, 0, 24),
}
deth_header = Header(deth_header_fields, deth_header_length, swap_field_bytes=True)

immdt_header_length = 4
immdt_header_fields = {
    "immdt": HeaderField(0, 0, 32)
}
immdt_header = Header(immdt_header_fields, immdt_header_length, swap_field_bytes=True)

ieth_header_length = 4
ieth_header_fields = {
    "r_key": HeaderField(0, 0, 32)
}
ieth_header = Header(ieth_header_fields, ieth_header_length, swap_field_bytes=True)

mad_header_length = 24
mad_header_fields = {
    "BaseVersion":       HeaderField(0, 0, 8),
    "MgmtClass":         HeaderField(1, 0, 8),
    "ClassVersion":      HeaderField(2, 0, 8),
    "R":                 HeaderField(3, 7, 1),
    "Method":            HeaderField(3, 0, 7),
    "Status":            HeaderField(4, 0, 16),
#   "ClassSpecific":     HeaderField(6, 0, 16), Unused
    "TransactionID":     HeaderField(8, 0, 64),
    "AttributeID":       HeaderField(16, 0, 16),
    "AttributeModifier": HeaderField(20, 0, 32),
}
mad_header = Header(mad_header_fields, mad_header_length, swap_field_bytes=True)

# ClassPortInfo
cpi_header_length = 72
cpi_header_fields = {
    "BaseVersion":       HeaderField(0,  0, 8),
    "ClassVersion":      HeaderField(1,  0, 8),
    "CapabilityMask":    HeaderField(2,  0, 16),
    "CapabilityMask2":   HeaderField(4,  0, 27),
    "RespTimeValue":     HeaderField(7,  0, 5),
#     "RedirectGID":       HeaderField(8,  0, 128),
#     "RedirectTC":        HeaderField(24, 0, 8),
#     "RedirectSL":        HeaderField(25, 4, 4),
#     "RedirectFL":        HeaderField(25, 0, 20),
#     "RedirectLID":       HeaderField(28, 0, 16),
#     "RedirectP_Key":     HeaderField(30, 0, 16),
# #   (reserved)
#     "RedirectQP":        HeaderField(33, 0, 24),
#     "RedirectQ_Key":     HeaderField(36, 0, 32),
#     "TrapGID":           HeaderField(40, 0, 128),
#     "TrapTC":            HeaderField(56, 0, 8),
#     "TrapSL":            HeaderField(57, 0, 4),
#     "TrapFL":            HeaderField(57, 4, 20),
#     "TrapLID":           HeaderField(60, 0, 16),
#     "TrapP_Key":         HeaderField(62, 0, 16),
#     "TrapHL":            HeaderField(64, 0, 8),
#     "TrapQP":            HeaderField(65, 0, 24),
#     "TrapQ_Key":         HeaderField(68, 0, 32),
}
cpi_header = Header(cpi_header_fields, cpi_header_length, swap_field_bytes=True)

req_header_length = 140
req_header_fields = {
    "Local_Communication_ID":      HeaderField(0,   0, 32),
#   (reserved)
    "ServiceID":                   HeaderField(8,   0, 64),
    "Local_CA_GUID":               HeaderField(16,  0, 64),
#   (reserved)
    "Local_Q_Key":                 HeaderField(28,  0, 32),
    "Local_QPN":                   HeaderField(32,  0, 24),
    "Responder_Resources":         HeaderField(35,  0, 8),
    # "Local_EECN":                  HeaderField(36,  0, 24),
    "Initiator_Depth":             HeaderField(39,  0, 8),
    # "Remote_EECN":                 HeaderField(40,  0, 24),
    "Remote_CM_Response_Timeout":  HeaderField(43,  3, 5),
    "Transport_Service_Type":      HeaderField(43,  1, 2),
    # "End_to_End_Flow_Control":     HeaderField(43,  0, 1),
    "Starting_PSN":                HeaderField(44,  0, 24),
    "Local_CM_Response_Timeout":   HeaderField(47,  3, 5),
    "Retry_Count":                 HeaderField(47,  0, 3),
    "Partition_Key":               HeaderField(48,  0, 16),
    "Path_Packet_Payload_MTU":     HeaderField(50,  4, 4),
    # "RDC_Exists":                  HeaderField(50,  3, 1),
    "RNR_Retry_Count":             HeaderField(50,  0, 3),
    "Max_CM_Retries":              HeaderField(51,  4, 4),
    # "SRQ":                         HeaderField(51,  3, 1),
#   (reserved)
###################################
#   "Primary_Local_Port_LID":      HeaderField(52,  0, 16), # ignored RoCEv2
#   "Primary_Remote_Port_LID":     HeaderField(54,  0, 16), # ignored RoCEv2
    "Primary_Local_Port_GID":      HeaderField(56,  0, 128),
    "Primary_Remote_Port_GID":     HeaderField(72,  0, 128),
    # "Primary_Flow_Label":          HeaderField(88,  0, 20),
#   (reserved)
    # "Primary_Packet_Rate":         HeaderField(91,  0, 6),
    # "Primary_Traffic_Class":       HeaderField(92,  0, 8),
    # "Primary_Hop_Limit":           HeaderField(93,  0, 8),
    # "Primary_SL":                  HeaderField(94,  4, 4),
    # "Primary_Subnet_Local":        HeaderField(94,  3, 1),
#   (reserved)
    "Primary_Local_ACK_Timeout":   HeaderField(95,  3, 5),
#   (reserved)
###################################
#   "Alternate_Local_Port_LID":    HeaderField(96,  0, 16), # ignored RoCEv2
#   "Alternate_Remote_Port_LID":   HeaderField(98,  0, 16), # ignored RoCEv2
    # "Alternate_Local_Port_GID":    HeaderField(100, 0, 128),
    # "Alternate_Remote_Port_GID":   HeaderField(116, 0, 128),
    # "Alternate_Flow_Label":        HeaderField(132, 0, 20),
#   (reserved)
    # "Alternate_Packet_Rate":       HeaderField(135, 0, 6),
    # "Alternate_Traffic_Class":     HeaderField(136, 0, 8),
    # "Alternate_Hop_Limit":         HeaderField(137, 0, 8),
    # "Alternate_SL":                HeaderField(138, 4, 4),
###################################
    # "Alternate_Subnet_Local":      HeaderField(138, 3, 1),
#   (reserved)
###################################
    # "Alternate_Local_ACK_Timeout": HeaderField(139, 3, 5),
#   (reserved)
}
req_header = Header(req_header_fields, req_header_length, swap_field_bytes=True)

mra_header_length = 10
mra_header_fields = {
    "Local_Communication_ID":  HeaderField(0, 0, 32),
    "Remote_Communication_ID": HeaderField(4, 0, 32),
    "Message_MRAed":           HeaderField(8, 0, 2),
#   (reserved)
    "ServiceTimeout":          HeaderField(9, 0, 5),
#   (reserved)
}
mra_header = Header(mra_header_fields, mra_header_length, swap_field_bytes=True)

rej_header_length = 84
rej_header_fields = {
    "Local_Communication_ID":  HeaderField(0, 0, 32),
    "Remote_Communication_ID": HeaderField(4, 0, 32),
    "Message_REJected":        HeaderField(8, 0, 2),
#   (reserved)
    "Reject_Info_Length":      HeaderField(9, 0, 7),
#   (reserved)
    "Reason":                  HeaderField(10, 0, 16),
    # "ARI":                     HeaderField(12, 0, 576),
}
rej_header = Header(rej_header_fields, rej_header_length, swap_field_bytes=True)

rep_header_length = 36
rep_header_fields = {
    "Local_Communication_ID":  HeaderField(0,  0, 32),
    "Remote_Communication_ID": HeaderField(4,  0, 32),
    "Local_Q_Key":             HeaderField(8,  0, 32),
    "Local_QPN":               HeaderField(12, 0, 24),
#   (reserved)
    # "Local_EE_Context_Number": HeaderField(16, 0, 24),
#   (reserved)
    "Starting_PSN":            HeaderField(20, 0, 24),
#   (reserved)
    "Responder_Resources":     HeaderField(24, 0, 8),
    "Initiator_Depth":         HeaderField(25, 0, 8),
    "Target_ACK_Delay":        HeaderField(26, 3, 5),
    # "Failover_Accepted":       HeaderField(26, 1, 2),
    # "End_To_End_Flow_Control": HeaderField(26, 0, 1),
    "RNR_Retry_Count":         HeaderField(27, 5, 3),
    # "SRQ":                     HeaderField(27, 4, 1),
#   (reserved)
    # "Local_CA_GUID":           HeaderField(28, 0, 64),
}
rep_header = Header(rep_header_fields, rep_header_length, swap_field_bytes=True)

rtu_header_length = 8
rtu_header_fields = {
    "Local_Communication_ID":  HeaderField(0, 0, 32),
    "Remote_Communication_ID": HeaderField(4, 0, 32),
}
rtu_header = Header(rtu_header_fields, rtu_header_length, swap_field_bytes=True)

dreq_header_length = 12
dreq_header_fields = {
    "Local_Communication_ID":  HeaderField(0, 0, 32),
    "Remote_Communication_ID": HeaderField(4, 0, 32),
    "Remote_QPN_EECN":         HeaderField(8, 0, 24),
#   (reserved)
}
dreq_header = Header(dreq_header_fields, dreq_header_length, swap_field_bytes=True)

drep_header_length = 8
drep_header_fields = {
    "Local_Communication_ID":  HeaderField(0, 0, 32),
    "Remote_Communication_ID": HeaderField(4, 0, 32),
}
drep_header = Header(drep_header_fields, drep_header_length, swap_field_bytes=True)


class MAD_ATTRIB_ID(IntEnum):
    ClassPortInfo         = 0x0001 ## unused
    ConnectRequest        = 0x0010 # req
    MsgRcptAck            = 0x0011 ## unused
    ConnectReject         = 0x0012 # rej
    ConnectReply          = 0x0013 # rep
    ReadyToUse            = 0x0014 # rtu
    DisconnectRequest     = 0x0015 # dreq
    DisconnectReply       = 0x0016 # drep
    ServiceIDResReq       = 0x0017 ## unused
    ServiceIDResReqResp   = 0x0018 ## unused
    LoadAlternatePath     = 0x0019 ## unused
    AlternatePathResponse = 0x000A ## unused

class CM_Methods(IntEnum):
    ComMgtGet     = 0x01
    ComMgtSet     = 0x02
    ComMgtGetResp = 0x81
    ComMgtSend    = 0x03

mad_cm_opmap = {
    MAD_ATTRIB_ID.ClassPortInfo         : 0b00000001,
    MAD_ATTRIB_ID.ConnectRequest        : 0b00000010,
    MAD_ATTRIB_ID.MsgRcptAck            : 0b00000100,
    MAD_ATTRIB_ID.ConnectReject         : 0b00001000,
    MAD_ATTRIB_ID.ConnectReply          : 0b00010000,
    MAD_ATTRIB_ID.ReadyToUse            : 0b00100000,
    MAD_ATTRIB_ID.DisconnectRequest     : 0b01000000,
    MAD_ATTRIB_ID.DisconnectReply       : 0b10000000,
    MAD_ATTRIB_ID.ServiceIDResReq       : 0b00000000,
    MAD_ATTRIB_ID.ServiceIDResReqResp   : 0b00000000,
    MAD_ATTRIB_ID.LoadAlternatePath     : 0b00000000,
    MAD_ATTRIB_ID.AlternatePathResponse : 0b00000000,
}
mad_cm_headers = [mad_header, cpi_header, req_header, mra_header, rej_header, rep_header, rtu_header, dreq_header, drep_header]

class QP_CONN_TYPE(IntEnum):
    RC = 0b000
    UC = 0b001 # Not implemented
    RD = 0b010 # Not implemented
    UD = 0b011

class BTH_OPCODE_OP(IntEnum):
    SEND_First                     = 0b00000
    SEND_Middle                    = 0b00001
    SEND_Last                      = 0b00010
    SEND_Last_with_Immediate       = 0b00011
    SEND_Only                      = 0b00100
    SEND_Only_with_Immediate       = 0b00101
    RDMA_WRITE_First               = 0b00110
    RDMA_WRITE_Middle              = 0b00111
    RDMA_WRITE_Last                = 0b01000
    RDMA_WRITE_Last_with_Immediate = 0b01001
    RDMA_WRITE_Only                = 0b01010
    RDMA_WRITE_Only_with_Immediate = 0b01011
    RDMA_READ_Request              = 0b01100
    RDMA_READ_response_First       = 0b01101
    RDMA_READ_response_Middle      = 0b01110
    RDMA_READ_response_Last        = 0b01111
    RDMA_READ_response_Only        = 0b10000
    Acknowledge                    = 0b10001
    # ATOMIC_Acknowledge             = 0b10010
    # CmpSwap                        = 0b10011
    # FetchAdd                       = 0b10100
    # SEND_Last_with_Invalidate      = 0b10110
    # SEND_Only_with_Invalidate      = 0b10111

class BTH_OPCODE:
    class RC(IntEnum):
        SEND_First                     = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_First
        SEND_Middle                    = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Middle
        SEND_Last                      = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Last
        SEND_Last_with_Immediate       = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Last_with_Immediate
        SEND_Only                      = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Only
        SEND_Only_with_Immediate       = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Only_with_Immediate
        RDMA_WRITE_First               = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_First
        RDMA_WRITE_Middle              = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_Middle
        RDMA_WRITE_Last                = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_Last
        RDMA_WRITE_Last_with_Immediate = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate
        RDMA_WRITE_Only                = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_Only
        RDMA_WRITE_Only_with_Immediate = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
        RDMA_READ_Request              = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_READ_Request
        RDMA_READ_response_First       = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_READ_response_First
        RDMA_READ_response_Middle      = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_READ_response_Middle
        RDMA_READ_response_Last        = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_READ_response_Last
        RDMA_READ_response_Only        = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.RDMA_READ_response_Only
        Acknowledge                    = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.Acknowledge
        # ATOMIC_Acknowledge             = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.ATOMIC_Acknowledge
        # CmpSwap                        = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.CmpSwap
        # FetchAdd                       = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.FetchAdd
        # SEND_Last_with_Invalidate      = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Last_with_Invalidate
        # SEND_Only_with_Invalidate      = (QP_CONN_TYPE.RC << 5) + BTH_OPCODE_OP.SEND_Only_with_Invalidate
    class UD(IntEnum):
        SEND_Only                      = (QP_CONN_TYPE.UD << 5) + BTH_OPCODE_OP.SEND_Only
        SEND_Only_with_Immediate       = (QP_CONN_TYPE.UD << 5) + BTH_OPCODE_OP.SEND_Only_with_Immediate

IBT_opmap = {
    # Reliable connection
    BTH_OPCODE.RC.SEND_First                     : 0b0000000,
    BTH_OPCODE.RC.SEND_Middle                    : 0b0000000,
    BTH_OPCODE.RC.SEND_Last                      : 0b0000000,
    BTH_OPCODE.RC.SEND_Last_with_Immediate       : 0b0000010,
    BTH_OPCODE.RC.SEND_Only                      : 0b0000000,
    BTH_OPCODE.RC.SEND_Only_with_Immediate       : 0b0000010,
    BTH_OPCODE.RC.RDMA_WRITE_First               : 0b0000100,
    BTH_OPCODE.RC.RDMA_WRITE_Middle              : 0b0000000,
    BTH_OPCODE.RC.RDMA_WRITE_Last                : 0b0000000,
    BTH_OPCODE.RC.RDMA_WRITE_Last_with_Immediate : 0b0000010,
    BTH_OPCODE.RC.RDMA_WRITE_Only                : 0b0000100,
    BTH_OPCODE.RC.RDMA_WRITE_Only_with_Immediate : 0b0000110,
    BTH_OPCODE.RC.RDMA_READ_Request              : 0b0000100,
    BTH_OPCODE.RC.RDMA_READ_response_First       : 0b0001000,
    BTH_OPCODE.RC.RDMA_READ_response_Middle      : 0b0000000,
    BTH_OPCODE.RC.RDMA_READ_response_Last        : 0b0001000,
    BTH_OPCODE.RC.RDMA_READ_response_Only        : 0b0001000,
    BTH_OPCODE.RC.Acknowledge                    : 0b0001000,
    # BTH_OPCODE.RC.ATOMIC_Acknowledge             : 0b0011000,
    # BTH_OPCODE.RC.CmpSwap                        : 0b0100000,
    # BTH_OPCODE.RC.FetchAdd                       : 0b0100000,
    # BTH_OPCODE.RC.SEND_Last_with_Invalidate      : 0b1000000,
    # BTH_OPCODE.RC.SEND_Only_with_Invalidate      : 0b1000000,

    # Unreliable datagram - necessary for communication establishment
    BTH_OPCODE.UD.SEND_Only                      : 0b0000001,
    BTH_OPCODE.UD.SEND_Only_with_Immediate       : 0b0000011,
}

IBT_RC_OPS = [op.value & 0b11111 for op in BTH_OPCODE.RC]
IBT_UD_OPS = [op.value & 0b11111 for op in BTH_OPCODE.UD]

IBT_headers = [bth_header, deth_header, immdt_header, reth_header, aeth_header]#, atomicacketh_header, atomiceth_header, ieth_header]
rocev2_port = 4791 # Assigned by IANA
DEFAULT_CM_Q_Key = 0x8001_0000
DEFAULT_P_KEY = 0xffff

PMTU_VALUES = {
    1: 256,
    2: 512,
    3: 1024,
    4: 2048,
    5: 4096
}
PMTU_KEY = 3
PMTU = PMTU_VALUES[PMTU_KEY] # Max size of transport layer packet payload
LOCAL_COMMUNICATION_ID = 0xfedcabed # MAD_CM Communication ID
RESPONDER_RESOURCES = 0x10 # Max number RDMA READ Requests that can be outstanding at once
STARTING_PSN = 0xfedfed


def add_params(description, params):
    payload_layout = description.payload_layout.copy()
    param_layout = description.param_layout.copy()
    param_layout += params
    return EndpointDescription(payload_layout, param_layout)

def is_in(signal, array):
    v = signal == array[0]
    for e in array[1:]:
        v = v | (signal == e)
    return v

def IBT_header_length(opcode):
    selection = IBT_opmap[opcode]
    return IBT_headers[0].length + (sum([header.length for i, header in list(enumerate(IBT_headers))[1:] if (selection >> (i - 1)) & 1]))

# Etherbone Constants/Header -----------------------------------------------------------------------

etherbone_magic                = 0x4e6f
etherbone_version              = 1
etherbone_packet_header_length = 8
etherbone_packet_header_fields = {
    "magic":     HeaderField(0, 0, 16),
    "version":   HeaderField(2, 4,  4),
    "nr":        HeaderField(2, 2,  1),
    "pr":        HeaderField(2, 1,  1),
    "pf":        HeaderField(2, 0,  1),
    "addr_size": HeaderField(3, 4,  4),
    "port_size": HeaderField(3, 0,  4)
}
etherbone_packet_header = Header(etherbone_packet_header_fields, etherbone_packet_header_length, swap_field_bytes=True)

etherbone_record_header_length = 4
etherbone_record_header_fields = {
    "bca":         HeaderField(0, 0, 1),
    "rca":         HeaderField(0, 1, 1),
    "rff":         HeaderField(0, 2, 1),
    "cyc":         HeaderField(0, 4, 1),
    "wca":         HeaderField(0, 5, 1),
    "wff":         HeaderField(0, 6, 1),
    "byte_enable": HeaderField(1, 0, 8),
    "wcount":      HeaderField(2, 0, 8),
    "rcount":      HeaderField(3, 0, 8)
}
etherbone_record_header = Header(etherbone_record_header_fields, etherbone_record_header_length, swap_field_bytes=True)

# Helpers ------------------------------------------------------------------------------------------

def _remove_from_layout(layout, *args):
    r = []
    for f in layout:
        remove = False
        for arg in args:
            if f[0] == arg:
                remove = True
        if not remove:
            r.append(f)
    return r

def convert_ip(s):
    if isinstance(s, str):
        ip = 0
        for e in s.split("."):
            ip = ip << 8
            ip += int(e)
        return ip
    else:
        return s

# Stream Layouts -----------------------------------------------------------------------------------

# PHY
def eth_phy_description(dw):
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout)

# MAC
def eth_mac_description(dw):
    payload_layout = mac_header.get_layout() + [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout)

# ARP
def eth_arp_description(dw):
    param_layout = arp_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

arp_table_request_layout = [
    ("ip_address", 32)
]

arp_table_response_layout = [
    ("failed",       1),
    ("mac_address", 48)
]

# IPV4
def eth_ipv4_description(dw):
    param_layout = ipv4_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_ipv4_user_description(dw):
    param_layout = [
        ("length",     16),
        ("protocol",    8),
        ("ip_address", 32)
    ]
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

# ICMP
def eth_icmp_description(dw):
    param_layout = icmp_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_icmp_user_description(dw):
    param_layout = icmp_header.get_layout() + [
        ("ip_address", 32),
        ("length",     16)
    ]
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

# UDP
def eth_udp_description(dw):
    param_layout = udp_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_udp_user_description(dw):
    param_layout = [
        ("src_port",   16),
        ("dst_port",   16),
        ("ip_address", 32),
        ("length",     16)
    ]
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

# RoCEv2
def eth_rocev2_description(dw):
    # Get unique fields from the various headers
    param_layout = list(set(sum([header.get_layout() for header in IBT_headers], []))) + [("length", 16)]
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout, param_layout)

def eth_mad_description(dw):
    # Get unique fields from the various headers
    param_layout = list(set(sum([header.get_layout() for header in mad_cm_headers], [])))
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout, param_layout)

def eth_rocev2_user_description(dw):
    param_layout = [
        ("dest_qp",  24),
        ("immdt",    32),
        ("length",   16)
    ]
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout, param_layout)

# Etherbone
def eth_etherbone_packet_description(dw):
    param_layout = etherbone_packet_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_packet_user_description(dw):
    param_layout = etherbone_packet_header.get_layout()
    param_layout = _remove_from_layout(param_layout, "magic", "portsize", "addrsize", "version")
    param_layout += eth_udp_user_description(dw).param_layout
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_record_description(dw):
    param_layout = etherbone_record_header.get_layout()
    payload_layout = [
        ("data",       dw),
        ("last_be", dw//8),
        ("error",   dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_mmap_description(dw):
    param_layout = [
        ("we",            1),
        ("count",         8),
        ("base_addr",    32),
        ("be",        dw//8)
    ]
    payload_layout = [
        ("addr",       32),
        ("last_be", dw//8),
        ("data",       dw)
    ]
    return EndpointDescription(payload_layout, param_layout)

# TTY
def eth_tty_tx_description(dw):
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)

def eth_tty_rx_description(dw):
    payload_layout = [("data", dw), ("error", 1)]
    return EndpointDescription(payload_layout)
