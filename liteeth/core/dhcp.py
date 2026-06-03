#
# This file is part of LiteEth.
#
# Copyright (c) 2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V.
# SPDX-License-Identifier: BSD-2-Clause

"""
DHCP

Minimal DHCP (IPV4) support for LiteEth.

Limitations/TODOs:
- No lease time parsing/support, user logic should consider it short (or known from server) and
issue a DHCP request regularly. Limitations is due to 32-bit data-path and parsing. Switching to a
8-bit data-path for DHCP options would allow supporting it more easily.
- Additional checks could be made on RX (see FIXMEs, but cost logic on FPGA).
- Define more DHCP constants and use them in the code.
"""

from migen import *

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from liteeth.common import *

# DHCP Constants -----------------------------------------------------------------------------------

DHCP_SERVER_PORT = 67
DHCP_CLIENT_PORT = 68

DHCP_FIXED_HEADER_LENGTH   = 236
DHCP_FIXED_DISCOVER_LENGTH = DHCP_FIXED_HEADER_LENGTH + 20
DHCP_FIXED_REQUEST_LENGTH  = DHCP_FIXED_HEADER_LENGTH + 32
DHCP_SERVER_NAME_LENGTH    = 64
DHCP_BOOT_FILE_NAME_LENGTH = 128

DHCP_TX_DISCOVER = 0b0
DHCP_TX_REQUEST  = 0b1

DHCP_RX_OFFER = 0b0
DHCP_RX_ACK   = 0b1

DHCP_OPTTYP_MESSAGE_TYPE          = 53
DHCP_OPTVAL_MESSAGE_TYPE_DISCOVER = 1
DHCP_OPTVAL_MESSAGE_TYPE_OFFER    = 2
DHCP_OPTVAL_MESSAGE_TYPE_REQUEST  = 3
DHCP_OPTVAL_MESSAGE_TYPE_ACK      = 5
DHCP_OPTTYP_REQ_IP_ADDRESS        = 50
DHCP_OPTTYP_SRV_IP_ADDRESS        = 54
DHCP_OPTTYP_LEASE_TIME            = 51
DHCP_OPTTYP_CLIENT_IDENTIFIER     = 61
DHCP_OPTTYP_PARAM_REQUEST_LIST    = 55
DHCP_OPTVAL_PARAM_SUBNET_MASK     = 1
DHCP_OPTVAL_PARAM_ROUTER          = 3
DHCP_OPTTYP_PAD                   = 0
DHCP_OPTTYP_END                   = 255

# DHCP TX ------------------------------------------------------------------------------------------

class LiteEthDHCPTX(LiteXModule):
    def __init__(self, udp_port):
        # Control/Status.
        self.start = Signal() # i
        self.done  = Signal() # o
        self.type  = Signal() # i

        # Parameters
        self.transaction_id     = Signal(32) # i
        self.mac_address        = Signal(48) # i
        self.server_ip_address  = Signal(32) # o (Only for Request).
        self.offered_ip_address = Signal(32) # o (Only for Request).

        # # #

        # Signals.
        # --------

        padding_len    = (8 + DHCP_SERVER_NAME_LENGTH + DHCP_BOOT_FILE_NAME_LENGTH) // 4
        count          = Signal(max=padding_len)
        longest_packet = max(DHCP_FIXED_DISCOVER_LENGTH, DHCP_FIXED_REQUEST_LENGTH) // 4
        length         = Signal(max=longest_packet)
        self.comb += Case(self.type, {
            DHCP_TX_DISCOVER : length.eq(DHCP_FIXED_DISCOVER_LENGTH // 4),
            DHCP_TX_REQUEST  : length.eq(DHCP_FIXED_REQUEST_LENGTH  // 4),
        })

        # Static Assign.
        # --------------
        self.comb += [
            udp_port.sink.src_port.eq(DHCP_CLIENT_PORT),
            udp_port.sink.dst_port.eq(DHCP_SERVER_PORT),
            udp_port.sink.ip_address.eq(convert_ip("255.255.255.255")),
            udp_port.sink.length.eq(length * 4),
            udp_port.sink.last_be.eq(0b1000), # 32-bit.
        ]

        # Common FSM.
        # -----------
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.start,
                NextState("HEADER")
            )
        )
        fsm.act("HEADER",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data[ 0: 8].eq(0x01), # Message Type: Boot Request (1).
            udp_port.sink.data[ 8:16].eq(0x01), # Hardware Type: Ethernet (1).
            udp_port.sink.data[16:24].eq(0x06), # Hardware Address Length: 6 bytes.
            udp_port.sink.data[24:32].eq(0x00), # Hops: 0.
            If(udp_port.sink.ready,
                NextState("TRANSACTION-ID")
            )
        )
        fsm.act("TRANSACTION-ID",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(self.transaction_id), # Transaction ID.
            If(udp_port.sink.ready,
                NextState("SECONDS-FLAGS")
            )
        )
        fsm.act("SECONDS-FLAGS",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data[ 0:16].eq(0x0000), # Seconds Elapsed: 0
            udp_port.sink.data[16:24].eq(0x80),   # Flags: Broadcast (0x8000)
            udp_port.sink.data[24:32].eq(0x00),
            If(udp_port.sink.ready,
                NextState("CLIENT-IP-ADDRESS")
            )
        )
        fsm.act("CLIENT-IP-ADDRESS",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(0x00000000), # Client IP: 0.0.0.0.
            If(udp_port.sink.ready,
                NextState("YOUR-IP-ADDRESS")
            )
        )
        fsm.act("YOUR-IP-ADDRESS",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(0x00000000), # Your IP: 0.0.0.0.
            If(udp_port.sink.ready,
                NextState("SERVER-IP-ADDRESS")
            )
        )
        fsm.act("SERVER-IP-ADDRESS",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(0x00000000), # Server IP: 0.0.0.0.
            If(udp_port.sink.ready,
                NextState("GATEWAY-IP-ADDRESS")
            )
        )
        fsm.act("GATEWAY-IP-ADDRESS",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(0x00000000), # Gateway IP: 0.0.0.0.
            If(udp_port.sink.ready,
                NextState("CLIENT-MAC-ADDRESS-MSB")
            )
        )
        fsm.act("CLIENT-MAC-ADDRESS-MSB", # Client MAC address MSBs.
            udp_port.sink.valid.eq(1),
            udp_port.sink.data[ 0: 8].eq(self.mac_address[40:48]),
            udp_port.sink.data[ 8:16].eq(self.mac_address[32:40]),
            udp_port.sink.data[16:24].eq(self.mac_address[24:32]),
            udp_port.sink.data[24:32].eq(self.mac_address[16:24]),
            If(udp_port.sink.ready,
                NextState("CLIENT-MAC-ADDRESS-LSB")
            )
        )
        fsm.act("CLIENT-MAC-ADDRESS-LSB", # Client MAC address LSBs.
            udp_port.sink.valid.eq(1),
            udp_port.sink.data[ 0: 8].eq(self.mac_address[ 8:16]),
            udp_port.sink.data[ 8:16].eq(self.mac_address[ 0: 8]),
            udp_port.sink.data[16:24].eq(0x00),
            udp_port.sink.data[24:32].eq(0x00),
            If(udp_port.sink.ready,
                NextValue(count, padding_len - 1),
                NextState("PADDING")
            )
        )
        # Padding, includes:
        #  - Client MAC padding.
        #  - Server name (Unused).
        #  - BOOT-FILE-NAME (Unused).
        fsm.act("PADDING",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data.eq(0x00000000),
            If(udp_port.sink.ready,
                NextValue(count, count - 1),
                If(count == 0,
                    NextState("MAGIC-COOKIE")
                )
            )
        )
        fsm.act("MAGIC-COOKIE",
            udp_port.sink.valid.eq(1),
            udp_port.sink.data[ 0: 8].eq(0x63),
            udp_port.sink.data[ 8:16].eq(0x82),
            udp_port.sink.data[16:24].eq(0x53),
            udp_port.sink.data[24:32].eq(0x63),
            If(udp_port.sink.ready,
                NextState("OPTIONS-0")
            )
        )
        # Options.
        # --------
        fsm.act("OPTIONS-0",
            udp_port.sink.valid.eq(1),
            # DHCP Message Type: Discover.
            udp_port.sink.data[ 0: 8].eq(DHCP_OPTTYP_MESSAGE_TYPE),
            udp_port.sink.data[ 8:16].eq(0x01),
            If(self.type == DHCP_TX_DISCOVER,
                udp_port.sink.data[16:24].eq(DHCP_OPTVAL_MESSAGE_TYPE_DISCOVER),
            ).Elif(self.type == DHCP_TX_REQUEST,
                udp_port.sink.data[16:24].eq(DHCP_OPTVAL_MESSAGE_TYPE_REQUEST),
            ),
            # Client Identifier.
            udp_port.sink.data[24:32].eq(DHCP_OPTTYP_CLIENT_IDENTIFIER),
            If(udp_port.sink.ready,
                NextState("OPTIONS-1")
            )
        )
        fsm.act("OPTIONS-1",
            udp_port.sink.valid.eq(1),
            # Client Identifier.
            udp_port.sink.data[ 0: 8].eq(0x06),
            udp_port.sink.data[ 8:16].eq(self.mac_address[40:48]),
            udp_port.sink.data[16:24].eq(self.mac_address[32:40]),
            udp_port.sink.data[24:32].eq(self.mac_address[24:32]),
            If(udp_port.sink.ready,
                NextState("OPTIONS-2")
            )
        )
        fsm.act("OPTIONS-2",
            udp_port.sink.valid.eq(1),
            # Client Identifier.
            udp_port.sink.data[ 0: 8].eq(self.mac_address[16:24]),
            udp_port.sink.data[ 8:16].eq(self.mac_address[ 8:16]),
            udp_port.sink.data[16:24].eq(self.mac_address[ 0: 8]),
            # Parameter Request List: Subnet Mask, Router.
            udp_port.sink.data[24:32].eq(DHCP_OPTTYP_PARAM_REQUEST_LIST),
            If(udp_port.sink.ready,
                NextState("OPTIONS-3")
            )
        )
        fsm.act("OPTIONS-3",
            udp_port.sink.valid.eq(1),
            # Parameter Request List: Subnet Mask, Router.
            udp_port.sink.data[ 0: 8].eq(0x02),
            udp_port.sink.data[ 8:16].eq(DHCP_OPTVAL_PARAM_SUBNET_MASK),
            udp_port.sink.data[16:24].eq(DHCP_OPTVAL_PARAM_ROUTER),
            If(self.type == DHCP_TX_DISCOVER,
                udp_port.sink.last.eq(1),
                udp_port.sink.data[24:32].eq(DHCP_OPTTYP_END),
                If(udp_port.sink.ready, NextState("DONE"))
            ).Elif(self.type == DHCP_TX_REQUEST,
                udp_port.sink.last.eq(0),
                udp_port.sink.data[24:32].eq(DHCP_OPTTYP_REQ_IP_ADDRESS),
                If(udp_port.sink.ready, NextState("OPTIONS-4"))
            )
        )
        # These options are only transmitted for DHCP REQUEST.
        fsm.act("OPTIONS-4",
            udp_port.sink.valid.eq(1),
            # Requested IP Address.
            udp_port.sink.data[ 0: 8].eq(0x04),
            udp_port.sink.data[ 8:16].eq(self.offered_ip_address[24:32]),
            udp_port.sink.data[16:24].eq(self.offered_ip_address[16:24]),
            udp_port.sink.data[24:32].eq(self.offered_ip_address[ 8:16]),
            If(udp_port.sink.ready,
                NextState("OPTIONS-5")
            )
        )
        fsm.act("OPTIONS-5",
            udp_port.sink.valid.eq(1),
            # Requested IP Address.
            udp_port.sink.data[ 0: 8].eq(self.offered_ip_address[0:8]),
            # Server IP Address.
            udp_port.sink.data[ 8:16].eq(DHCP_OPTTYP_SRV_IP_ADDRESS),
            udp_port.sink.data[16:24].eq(0x04),
            udp_port.sink.data[24:32].eq(self.server_ip_address[24:32]),
            If(udp_port.sink.ready,
                NextState("OPTIONS-6")
            )
        )
        fsm.act("OPTIONS-6",
            udp_port.sink.last.eq(1),
            udp_port.sink.valid.eq(1),
            # Server IP Address.
            udp_port.sink.data[ 0: 8].eq(self.server_ip_address[16:24]),
            udp_port.sink.data[ 8:16].eq(self.server_ip_address[ 8:16]),
            udp_port.sink.data[16:24].eq(self.server_ip_address[ 0: 8]),
            # Client Identifier.
            udp_port.sink.data[24:32].eq(DHCP_OPTTYP_END),
            If(udp_port.sink.ready,
                NextState("DONE")
            )
        )
        # Done.
        # -----
        fsm.act("DONE",
            self.done.eq(1),
            NextState("IDLE")
        )

# DHCP Response/ACK --------------------------------------------------------------------------------

class LiteEthDHCPRX(LiteXModule):
    def __init__(self, udp_port):
        # Control/Status.
        self.present = Signal() # o
        self.ack     = Signal() # i
        self.type    = Signal() # o
        self.error   = Signal() # o

        # Parameters
        self.transaction_id     = Signal(32) # i
        self.mac_address        = Signal(48) # i
        self.server_ip_address  = Signal(32) # o
        self.offered_ip_address = Signal(32) # o

        # TODO: Parse more DHCP Options.
        # self.gateway_ip_address = Signal(32)
        # self.subnet_mask        = Signal(32)
        # self.router             = Signal(32)
        # self.lease_time         = Signal(32)

        # # #

        # Signals.
        # --------

        padding_len        = (8 + DHCP_SERVER_NAME_LENGTH + DHCP_BOOT_FILE_NAME_LENGTH) // 4
        count              = Signal(max=padding_len)
        option_word        = Signal(32)
        option_last        = Signal()
        option_byte_index  = Signal(2)
        option_byte        = Signal(8)
        option_code        = Signal(8)
        option_length      = Signal(8)
        option_count       = Signal(8)
        option_value_index = Signal(8)
        message_type_seen  = Signal()

        self.comb += Case(option_byte_index, {
            0 : option_byte.eq(option_word[ 0: 8]),
            1 : option_byte.eq(option_word[ 8:16]),
            2 : option_byte.eq(option_word[16:24]),
            3 : option_byte.eq(option_word[24:32]),
        })

        def advance_option_byte(next_state, packet_end=None):
            if packet_end is None:
                packet_end = [NextState("ERROR")]
            load_state = {
                "OPTION-CODE"   : "OPTIONS-WORD",
                "OPTION-LENGTH" : "OPTIONS-WORD-LENGTH",
                "OPTION-VALUE"  : "OPTIONS-WORD-VALUE",
            }[next_state]
            return If(option_byte_index == 3,
                If(option_last,
                    *packet_end
                ).Else(
                    NextValue(option_byte_index, 0),
                    NextState(load_state)
                )
            ).Else(
                NextValue(option_byte_index, option_byte_index + 1),
                NextState(next_state)
            )

        def finish_option_value():
            return If(option_count == 1,
                NextValue(option_count, 0),
                NextValue(option_value_index, 0),
                advance_option_byte("OPTION-CODE")
            ).Else(
                NextValue(option_count, option_count - 1),
                NextValue(option_value_index, option_value_index + 1),
                advance_option_byte("OPTION-VALUE")
            )

        # Common FSM.
        # -----------
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                If((udp_port.source.dst_port == DHCP_CLIENT_PORT) &
                   (udp_port.source.src_port == DHCP_SERVER_PORT) &
                   # Fixed header + magic_cookie + message_type.
                   (udp_port.source.length >= DHCP_FIXED_HEADER_LENGTH + 4 + 4),
                    udp_port.source.ready.eq(0),
                    NextValue(message_type_seen, 0),
                    NextState("HEADER")
                ).Else(
                    NextState("DROP")
                )
            )
        )
        fsm.act("HEADER",
            # FIXME: Add Check?
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextState("TRANSACTION-ID")
            )
        )
        fsm.act("TRANSACTION-ID",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                If(udp_port.source.data == self.transaction_id,
                    NextState("SECONDS-FLAGS")
                ).Else(
                    NextState("DROP")
                )
            )
        )
        fsm.act("SECONDS-FLAGS",
            # FIXME: Add Check?
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextState("CLIENT-IP-ADDRESS")
            )
        )
        fsm.act("CLIENT-IP-ADDRESS",
            # FIXME: Add Check?
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextState("YOUR-IP-ADDRESS")
            )
        )
        fsm.act("YOUR-IP-ADDRESS",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(self.offered_ip_address[24:32], udp_port.source.data[ 0: 8]),
                NextValue(self.offered_ip_address[16:24], udp_port.source.data[ 8:16]),
                NextValue(self.offered_ip_address[ 8:16], udp_port.source.data[16:24]),
                NextValue(self.offered_ip_address[ 0: 8], udp_port.source.data[24:32]),
                NextState("SERVER-IP-ADDRESS")
            ),
        )
        fsm.act("SERVER-IP-ADDRESS",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(self.server_ip_address[24:32], udp_port.source.data[ 0: 8]),
                NextValue(self.server_ip_address[16:24], udp_port.source.data[ 8:16]),
                NextValue(self.server_ip_address[ 8:16], udp_port.source.data[16:24]),
                NextValue(self.server_ip_address[ 0: 8], udp_port.source.data[24:32]),
                NextState("GATEWAY-IP-ADDRESS")
            )
        )
        fsm.act("GATEWAY-IP-ADDRESS",
            # FIXME: Add Check?
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextState("CLIENT-MAC-ADDRESS-MSB")
            )
        )
        fsm.act("CLIENT-MAC-ADDRESS-MSB", # Client MAC address MSBs.
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                If((udp_port.source.data[ 0: 8] == self.mac_address[40:48]) &
                   (udp_port.source.data[ 8:16] == self.mac_address[32:40]) &
                   (udp_port.source.data[16:24] == self.mac_address[24:32]) &
                   (udp_port.source.data[24:32] == self.mac_address[16:24]),
                    NextState("CLIENT-MAC-ADDRESS-LSB"),
                ).Else(
                    NextState("DROP")
                )
            )
        )
        fsm.act("CLIENT-MAC-ADDRESS-LSB", # Client MAC address LSBs.
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                If((udp_port.source.data[ 0: 8] == self.mac_address[ 8:16]) &
                   (udp_port.source.data[ 8:16] == self.mac_address[ 0: 8]),
                    NextValue(count, padding_len - 1),
                    NextState("PADDING"),
                ).Else(
                    NextState("DROP")
                )
            )
        )
        # Padding, includes:
        #  - Client MAC padding.
        #  - Server name (Unused).
        #  - BOOT-FILE-NAME (Unused).
        fsm.act("PADDING",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(count, count - 1),
                If(udp_port.source.last,
                    NextState("ERROR")
                ).Elif(count == 0,
                    NextState("MAGIC-COOKIE")
                )
            )
        )
        fsm.act("MAGIC-COOKIE",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                If((udp_port.source.data[ 0: 8] == 0x63) &
                   (udp_port.source.data[ 8:16] == 0x82) &
                   (udp_port.source.data[16:24] == 0x53) &
                   (udp_port.source.data[24:32] == 0x63),
                    NextState("OPTIONS-WORD")
                ).Else(
                    If(udp_port.source.last,
                        NextState("ERROR")
                    ).Else(
                        NextState("DROP")
                    )
                )
            )
        )
        fsm.act("OPTIONS-WORD",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(option_word, udp_port.source.data),
                NextValue(option_last, udp_port.source.last),
                NextValue(option_byte_index, 0),
                NextState("OPTION-CODE")
            )
        )
        fsm.act("OPTIONS-WORD-LENGTH",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(option_word, udp_port.source.data),
                NextValue(option_last, udp_port.source.last),
                NextValue(option_byte_index, 0),
                NextState("OPTION-LENGTH")
            )
        )
        fsm.act("OPTIONS-WORD-VALUE",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid,
                NextValue(option_word, udp_port.source.data),
                NextValue(option_last, udp_port.source.last),
                NextValue(option_byte_index, 0),
                NextState("OPTION-VALUE")
            )
        )
        fsm.act("OPTION-CODE",
            If(option_byte == DHCP_OPTTYP_PAD,
                advance_option_byte("OPTION-CODE",
                    packet_end=[If(message_type_seen, NextState("PRESENT")).Else(NextState("ERROR"))])
            ).Elif(option_byte == DHCP_OPTTYP_END,
                If(message_type_seen,
                    If(option_last,
                        NextState("PRESENT")
                    ).Else(
                        NextState("END")
                    )
                ).Else(
                    NextState("ERROR")
                )
            ).Else(
                NextValue(option_code, option_byte),
                advance_option_byte("OPTION-LENGTH")
            )
        )
        fsm.act("OPTION-LENGTH",
            If((option_code == DHCP_OPTTYP_MESSAGE_TYPE) & (option_byte != 0x01),
                NextState("ERROR")
            ).Else(
                NextValue(option_length, option_byte),
                NextValue(option_count, option_byte),
                NextValue(option_value_index, 0),
                If(option_byte == 0,
                    advance_option_byte("OPTION-CODE")
                ).Else(
                    advance_option_byte("OPTION-VALUE")
                )
            )
        )
        fsm.act("OPTION-VALUE",
            If(option_code == DHCP_OPTTYP_MESSAGE_TYPE,
                If(option_byte == DHCP_OPTVAL_MESSAGE_TYPE_OFFER,
                    NextValue(self.type, DHCP_RX_OFFER),
                    NextValue(message_type_seen, 1),
                    finish_option_value()
                ).Elif(option_byte == DHCP_OPTVAL_MESSAGE_TYPE_ACK,
                    NextValue(self.type, DHCP_RX_ACK),
                    NextValue(message_type_seen, 1),
                    finish_option_value()
                ).Else(
                    NextState("ERROR")
                )
            ).Else(
                If((option_code == DHCP_OPTTYP_SRV_IP_ADDRESS) & (option_length == 4),
                    Case(option_value_index, {
                        0 : NextValue(self.server_ip_address[24:32], option_byte),
                        1 : NextValue(self.server_ip_address[16:24], option_byte),
                        2 : NextValue(self.server_ip_address[ 8:16], option_byte),
                        3 : NextValue(self.server_ip_address[ 0: 8], option_byte),
                    })
                ),
                finish_option_value()
            )
        )
        fsm.act("END",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid & udp_port.source.last,
                NextState("PRESENT")
            )
        )
        fsm.act("ERROR",
            NextValue(self.error, 1),
            NextState("PRESENT")
        )
        fsm.act("DROP",
            udp_port.source.ready.eq(1),
            If(udp_port.source.valid & udp_port.source.last,
                NextState("ERROR")
            )
        )
        fsm.act("PRESENT",
            self.present.eq(1),
            If(self.ack,
                NextValue(self.error, 0),
                NextState("IDLE")
            )
        )

# DHCP ---------------------------------------------------------------------------------------------

class LiteEthDHCP(LiteXModule):
    def __init__(self, udp_port, sys_clk_freq, timeout=1e0):
        assert len(udp_port.sink.data) == 32 # Only supports 32-bit UDP port.

        # Control/Status.
        self.start   = Signal() # i
        self.done    = Signal() # o
        self.timeout = Signal() # o

        # Parameters
        self.mac_address = Signal(48) # i
        self.ip_address  = Signal(32) # o

        # # #

        # Signals.
        transaction_id     = Signal(32)
        offered_ip_address = Signal(32)
        server_ip_address  = Signal(32)

        # DHCP TX.
        self.tx = tx = ResetInserter()(LiteEthDHCPTX(udp_port))
        self.comb += [
            tx.reset.eq(self.timeout),
            tx.mac_address.eq(self.mac_address),
            tx.transaction_id.eq(transaction_id),
        ]

        # DHCP RX.
        self.rx = rx = ResetInserter()(LiteEthDHCPRX(udp_port))
        self.comb += [
            rx.reset.eq(self.timeout),
            rx.mac_address.eq(self.mac_address),
            rx.transaction_id.eq(transaction_id),
        ]

        # DHCP Timeout.
        self.timeout_timer = timeout_timer = WaitTimer(int(timeout*sys_clk_freq))
        self.comb += [
            timeout_timer.wait.eq(~self.done),
            self.timeout.eq(timeout_timer.done),
        ]

        # DHCP FSM.
        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += fsm.reset.eq(self.timeout)
        fsm.act("IDLE",
            self.done.eq(1),
            If(self.start,
                NextValue(transaction_id, transaction_id + 1),
                NextState("SEND-DISCOVER")
            )
        )
        fsm.act("SEND-DISCOVER",
            tx.start.eq(1),
            tx.type.eq(DHCP_TX_DISCOVER),
            If(tx.done,
                NextState("RECEIVE-OFFER")
            )
        )
        fsm.act("RECEIVE-OFFER",
            rx.ack.eq(1),
            If(rx.present & ~rx.error & (rx.type == DHCP_RX_OFFER),
                NextValue(offered_ip_address, rx.offered_ip_address),
                NextValue(server_ip_address,  rx.server_ip_address),
                NextState("SEND-REQUEST")
            )
        )
        fsm.act("SEND-REQUEST",
            tx.start.eq(1),
            tx.type.eq(DHCP_TX_REQUEST),
            tx.offered_ip_address.eq(offered_ip_address),
            tx.server_ip_address.eq(server_ip_address),
            If(tx.done,
                NextState("RECEIVE-ACK")
            )
        )
        fsm.act("RECEIVE-ACK",
            rx.ack.eq(1),
            If(rx.present & ~rx.error & (rx.type == DHCP_RX_ACK),
                NextValue(self.ip_address, offered_ip_address),
                NextState("IDLE")
            )
        )
