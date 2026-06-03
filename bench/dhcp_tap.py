#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import queue
import socket
import struct
import argparse
import threading

from migen import *

from litex.gen import *

from litex.build.generic_platform import *
from litex.build.sim import SimPlatform
from litex.build.sim.config import SimConfig
from litex.soc.integration.builder import Builder
from litex.soc.integration.soc import SoCCore, SoCMini

from liteeth.common import *
from liteeth.core import LiteEthUDPIPCore
from liteeth.core.dhcp import *
from liteeth.frontend.etherbone import LiteEthEtherbone
from liteeth.phy.model import LiteEthPHYModel

# IOs ----------------------------------------------------------------------------------------------

_io = [
    # Sys Clk/Rst.
    ("sys_clk", 0, Pins(1)),
    ("sys_rst", 0, Pins(1)),
    # Ethernet.
    ("eth_clocks", 0,
        Subsignal("tx", Pins(1)),
        Subsignal("rx", Pins(1)),
    ),
    ("eth", 0,
        Subsignal("source_valid", Pins(1)),
        Subsignal("source_ready", Pins(1)),
        Subsignal("source_data",  Pins(8)),

        Subsignal("sink_valid",   Pins(1)),
        Subsignal("sink_ready",   Pins(1)),
        Subsignal("sink_data",    Pins(8)),
    ),
]

# Helpers ------------------------------------------------------------------------------------------

def ip_bytes(ip):
    return bytes(int(part) for part in ip.split("."))

def mac_bytes(mac):
    if isinstance(mac, int):
        return mac.to_bytes(6, "big")
    return bytes(int(part, 16) for part in mac.split(":"))

def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    value = 0
    for offset in range(0, len(data), 2):
        value += (data[offset] << 8) | data[offset + 1]
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return (~value) & 0xffff

def get_option(options, code):
    offset = 0
    while offset < len(options):
        option = options[offset]
        offset += 1
        if option == DHCP_OPTTYP_END:
            return None
        if option == DHCP_OPTTYP_PAD:
            continue
        if offset >= len(options):
            return None
        length = options[offset]
        offset += 1
        value = options[offset:offset + length]
        offset += length
        if option == code:
            return value
    return None

def build_dhcp_payload(message_type, xid, client_mac, offered_ip, server_ip):
    server_ip_b = ip_bytes(server_ip)
    payload = bytearray()
    payload += b"\x02\x01\x06\x00" # BOOTP reply, Ethernet, 6-byte MAC, no hops.
    payload += xid
    payload += b"\x00\x00\x00\x00" # Seconds + flags.
    payload += b"\x00\x00\x00\x00" # ciaddr.
    payload += ip_bytes(offered_ip) # yiaddr.
    payload += b"\x00\x00\x00\x00" # siaddr; server identifier option is authoritative.
    payload += b"\x00\x00\x00\x00" # giaddr.
    payload += client_mac
    payload += b"\x00"*(16 - len(client_mac))
    payload += b"\x00"*DHCP_SERVER_NAME_LENGTH
    payload += b"\x00"*DHCP_BOOT_FILE_NAME_LENGTH
    payload += b"\x63\x82\x53\x63"
    payload += bytes([
        DHCP_OPTTYP_SRV_IP_ADDRESS, 4, *server_ip_b,
        DHCP_OPTTYP_LEASE_TIME,     4, 0x00, 0x00, 0x0e, 0x10,
        DHCP_OPTVAL_PARAM_SUBNET_MASK, 4, 255, 255, 255, 0,
        DHCP_OPTVAL_PARAM_ROUTER,      4, *server_ip_b,
        DHCP_OPTTYP_MESSAGE_TYPE,   1, message_type,
        DHCP_OPTTYP_END,
    ])
    return bytes(payload)

def build_udp_frame(dst_mac, src_mac, src_ip, dst_ip, src_port, dst_port, payload):
    ip_header_len = 20
    udp_header_len = 8
    total_length = ip_header_len + udp_header_len + len(payload)
    udp_length = udp_header_len + len(payload)

    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0x00, total_length, 0x0000, 0x0000, 64, udp_protocol, 0x0000,
        ip_bytes(src_ip), ip_bytes(dst_ip))
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]

    udp_header = struct.pack("!HHHH", src_port, dst_port, udp_length, 0x0000)
    return (
        dst_mac +
        src_mac +
        struct.pack("!H", ethernet_type_ip) +
        ip_header +
        udp_header +
        payload
    )

# DHCP Server --------------------------------------------------------------------------------------

class DHCPServer:
    def __init__(self, interface, server_mac, server_ip, offered_ip, stop_event):
        self.interface  = interface
        self.server_mac = mac_bytes(server_mac)
        self.server_ip  = server_ip
        self.offered_ip = offered_ip
        self.stop_event = stop_event

    def run(self):
        if not hasattr(socket, "AF_PACKET"):
            raise OSError("AF_PACKET raw sockets are only available on Linux")

        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ethernet_type_ip))
        sock.bind((self.interface, 0))
        sock.settimeout(0.2)

        print(f"[dhcp-server] listening on {self.interface}")
        while not self.stop_event.is_set():
            try:
                frame = sock.recv(2048)
            except socket.timeout:
                continue

            request = self.decode_request(frame)
            if request is None:
                continue

            message_type, xid, client_mac = request
            if message_type == DHCP_OPTVAL_MESSAGE_TYPE_DISCOVER:
                reply_type = DHCP_OPTVAL_MESSAGE_TYPE_OFFER
                label = "DISCOVER"
            elif message_type == DHCP_OPTVAL_MESSAGE_TYPE_REQUEST:
                reply_type = DHCP_OPTVAL_MESSAGE_TYPE_ACK
                label = "REQUEST"
            else:
                continue

            payload = build_dhcp_payload(
                message_type = reply_type,
                xid          = xid,
                client_mac   = client_mac,
                offered_ip   = self.offered_ip,
                server_ip    = self.server_ip,
            )
            reply = build_udp_frame(
                dst_mac  = b"\xff"*6,
                src_mac  = self.server_mac,
                src_ip   = self.server_ip,
                dst_ip   = "255.255.255.255",
                src_port = DHCP_SERVER_PORT,
                dst_port = DHCP_CLIENT_PORT,
                payload  = payload,
            )
            sock.send(reply)
            print(f"[dhcp-server] {label} -> {'OFFER' if reply_type == DHCP_OPTVAL_MESSAGE_TYPE_OFFER else 'ACK'}")

        sock.close()

    def decode_request(self, frame):
        if len(frame) < 14 + 20 + 8:
            return None
        if frame[12:14] != struct.pack("!H", ethernet_type_ip):
            return None

        ip_offset = 14
        ip_header = frame[ip_offset:]
        if ip_header[0] >> 4 != 4:
            return None
        if ip_header[9] != udp_protocol:
            return None

        ihl = (ip_header[0] & 0x0f)*4
        udp_offset = ip_offset + ihl
        udp_header = frame[udp_offset:udp_offset + 8]
        src_port, dst_port, udp_length, _ = struct.unpack("!HHHH", udp_header)
        if src_port != DHCP_CLIENT_PORT or dst_port != DHCP_SERVER_PORT:
            return None

        payload = frame[udp_offset + 8:udp_offset + udp_length]
        if len(payload) < DHCP_FIXED_HEADER_LENGTH + 4 + 4:
            return None
        if payload[0] != 0x01:
            return None
        if payload[236:240] != b"\x63\x82\x53\x63":
            return None

        message_type = get_option(payload[240:], DHCP_OPTTYP_MESSAGE_TYPE)
        if message_type is None or len(message_type) != 1:
            return None

        return message_type[0], payload[4:8], payload[28:34]

# Simulation SoC -----------------------------------------------------------------------------------

class Platform(SimPlatform):
    def __init__(self):
        SimPlatform.__init__(self, "SIM", _io)

class DHCPBenchSoC(SoCCore):
    def __init__(self, mac_address, etherbone, timeout):
        platform = Platform()
        sys_clk_freq = int(1e6)

        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq,
            ident         = "LiteEth DHCP bench Simulation",
            ident_version = True,
        )

        self.crg = CRG(platform.request("sys_clk"))

        self.ethphy = LiteEthPHYModel(self.platform.request("eth"))
        self.ip_address = Signal(32, reset=0)
        self.core = LiteEthUDPIPCore(
            phy               = self.ethphy,
            mac_address       = mac_address,
            ip_address        = self.ip_address,
            clk_freq          = sys_clk_freq,
            dw                = 8,
            with_icmp         = False,
            with_ip_broadcast = True,
        )

        if etherbone:
            self.etherbone = LiteEthEtherbone(self.core.udp, udp_port=1234, buffer_depth=16, cd="sys")
            self.bus.add_master(name="etherbone", master=self.etherbone.wishbone.bus)
            self.add_ram("sram", 0x20000000, 0x1000)

        dhcp_port = self.core.udp.crossbar.get_port(DHCP_CLIENT_PORT, dw=32, cd="sys")
        self.dhcp = LiteEthDHCP(udp_port=dhcp_port, sys_clk_freq=sys_clk_freq, timeout=timeout)
        self.comb += self.dhcp.mac_address.eq(mac_address)

        start_counter = Signal(8)
        started       = Signal()
        finished      = Signal()
        self.sync += [
            self.dhcp.start.eq(0),
            If(~started,
                start_counter.eq(start_counter + 1),
                If(start_counter == 16,
                    Display("DHCP_START"),
                    self.dhcp.start.eq(1),
                    started.eq(1),
                )
            ).Elif(~finished,
                If(self.dhcp.timeout,
                    Display("DHCP_TIMEOUT"),
                    finished.eq(1),
                    Finish(),
                ).Elif(self.dhcp.done & (self.dhcp.ip_address != 0),
                    self.ip_address.eq(self.dhcp.ip_address),
                    Display("DHCP_DONE ip=%08x", self.dhcp.ip_address),
                    finished.eq(1),
                    Finish(),
                )
            )
        ]

# Main ---------------------------------------------------------------------------------------------

def run_sim(args):
    sim_config = SimConfig()
    sim_config.add_clocker("sys_clk", freq_hz=1e6)
    sim_config.add_module("ethernet", "eth", args={"interface": args.interface, "ip": args.server_ip})

    soc = DHCPBenchSoC(
        mac_address = int(args.client_mac.replace(":", ""), 16),
        etherbone   = not args.no_etherbone,
        timeout     = args.timeout,
    )
    builder = Builder(soc,
        output_dir = args.output_dir,
        csr_csv    = os.path.join(args.output_dir, "csr.csv"),
    )
    builder.build(sim_config=sim_config)

def main():
    parser = argparse.ArgumentParser(description="LiteEth DHCP TAP integration bench")
    parser.add_argument("--interface",     default="tap0",              help="TAP interface for the LiteX ethernet sim module.")
    parser.add_argument("--client-mac",    default="10:e2:d5:00:00:01", help="Simulated LiteEth client MAC address.")
    parser.add_argument("--server-mac",    default="10:e2:d5:00:00:ff", help="Emulated DHCP server MAC address.")
    parser.add_argument("--server-ip",     default="192.168.1.1",       help="Emulated DHCP server/router IPv4 address.")
    parser.add_argument("--offered-ip",    default="192.168.1.50",      help="IPv4 address offered to the simulated client.")
    parser.add_argument("--timeout",       default=0.25, type=float,    help="DHCP core timeout in simulation seconds.")
    parser.add_argument("--output-dir",    default="build/dhcp_tap",    help="LiteX simulation build output directory.")
    parser.add_argument("--no-etherbone",  action="store_true",         help="Do not instantiate Etherbone on the DHCP UDP/IP core.")
    parser.add_argument("--server-only",   action="store_true",         help="Only run the raw-socket DHCP server emulator.")
    parser.add_argument("--no-server",     action="store_true",         help="Run only the simulation; use an external DHCP server.")
    args = parser.parse_args()

    stop_event = threading.Event()
    server_thread = None
    server_errors = queue.Queue()

    if not args.no_server:
        server = DHCPServer(
            interface  = args.interface,
            server_mac = args.server_mac,
            server_ip  = args.server_ip,
            offered_ip = args.offered_ip,
            stop_event = stop_event,
        )

        def server_entry():
            try:
                server.run()
            except Exception as e:
                server_errors.put(e)
                stop_event.set()

        server_thread = threading.Thread(target=server_entry, daemon=True)
        server_thread.start()

    try:
        if args.server_only:
            while True:
                if not server_errors.empty():
                    raise server_errors.get()
                stop_event.wait(1.0)
        else:
            if server_thread is not None:
                stop_event.wait(0.5)
                if not server_errors.empty():
                    raise server_errors.get()
            run_sim(args)
    finally:
        stop_event.set()
        if server_thread is not None:
            server_thread.join(timeout=1.0)

if __name__ == "__main__":
    main()
