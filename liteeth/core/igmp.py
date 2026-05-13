#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer

from liteeth.common import *

# Constants ----------------------------------------------------------------------------------------

igmp_protocol = 0x02

# Helpers ------------------------------------------------------------------------------------------

def igmp_checksum(group_ip):
    """Compute IGMPv2 Membership Report checksum for a group address."""
    s = 0x1600 + ((group_ip >> 16) & 0xFFFF) + (group_ip & 0xFFFF)
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

# IGMP Joiner --------------------------------------------------------------------------------------

class LiteEthIGMPJoiner(LiteXModule):
    """
    IGMP Multicast Group Joiner.

    Periodically sends IGMPv2 Membership Reports for the specified multicast
    groups via the IP crossbar (protocol=2). The IP layer handles header
    construction and multicast MAC resolution.

    Parameters:
    - ip           : LiteEthIP instance.
    - groups       : List of multicast group IPv4 addresses as integers.
    - interval     : Report interval in seconds (default 10).
    - sys_clk_freq : System clock frequency.
    - dw           : Optional IP datapath width; defaults to the IP crossbar width.
    - enable       : Optional sys-domain enable/link-ready signal.
    """
    def __init__(self, ip, groups, interval=10, sys_clk_freq=int(100e6), dw=None, enable=1):
        if dw is None:
            dw = ip.crossbar.master.dw
        self.dw = dw

        # # #

        # IP Port (protocol=2 IGMP).
        # --------------------------
        ip_port = ip.crossbar.get_port(igmp_protocol, dw=dw)

        # Discard incoming IGMP packets (queries).
        self.comb += ip_port.source.ready.eq(1)

        source = stream.Endpoint(eth_ipv4_user_description(8))
        if dw == 8:
            self.comb += source.connect(ip_port.sink)
        else:
            self.converter = stream.StrideConverter(
                eth_ipv4_user_description(8),
                eth_ipv4_user_description(dw),
            )
            self.comb += [
                source.connect(self.converter.sink),
                self.converter.source.connect(ip_port.sink),
            ]

        # Pre-compute IGMP payloads.
        # --------------------------
        igmp_len  = 8
        n_groups  = len(groups)
        total     = n_groups * igmp_len

        # Build flat byte table and group IP table.
        all_bytes = []
        all_ips   = []
        for group_ip in groups:
            cksum = igmp_checksum(group_ip)
            all_bytes.extend([
                0x16, 0x00,
                (cksum >> 8) & 0xFF, cksum & 0xFF,
                (group_ip >> 24) & 0xFF, (group_ip >> 16) & 0xFF,
                (group_ip >>  8) & 0xFF, (group_ip >>  0) & 0xFF,
            ])
            all_ips.append(group_ip)

        # Byte/IP lookup via Case (pure combinational).
        count     = Signal(max=max(2, total))
        byte_idx  = Signal(max=max(2, igmp_len))
        group_idx = Signal(max=max(2, n_groups))
        igmp_data = Signal(8)
        igmp_ip   = Signal(32)

        self.comb += [
            byte_idx.eq(count[:3]),
            group_idx.eq(count[3:]),
            Case(count, {
                i: igmp_data.eq(b) for i, b in enumerate(all_bytes)
            }),
            Case(group_idx, {
                i: igmp_ip.eq(g) for i, g in enumerate(all_ips)
            }),
        ]

        # Timer.
        # ------
        timer = WaitTimer(int(interval * sys_clk_freq))
        self.submodules += timer

        last = Signal()
        self.comb += last.eq(byte_idx == (igmp_len - 1))

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="WAIT")

        # WAIT: wait for timer to expire.
        fsm.act("WAIT",
            timer.wait.eq(enable),
            If(enable & timer.done,
                NextValue(count, 0),
                NextState("SEND"),
            ),
        )

        # SEND: drive data directly from combinational Case lookup.
        fsm.act("SEND",
            source.valid.eq(1),
            source.last.eq(last),
            source.last_be.eq(last),
            source.error.eq(0),
            source.data.eq(igmp_data),
            source.ip_address.eq(igmp_ip),
            source.protocol.eq(igmp_protocol),
            source.length.eq(igmp_len),
            If(source.ready,
                NextValue(count, count + 1),
                If(last,
                    If(count == (total - 1),
                        NextState("WAIT"),
                    ).Else(
                        NextState("GAP"),
                    ),
                ),
            ),
        )

        # GAP: 1-cycle idle between reports (deassert valid).
        fsm.act("GAP",
            NextState("SEND"),
        )
