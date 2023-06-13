#!/usr/bin/env python3

#
# This file is part of LiteEth
#
# Copyright (c) 2021-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# LiteEth UDP Streamer test utility.

import socket
import time
import argparse
import datetime

# Leds Test ----------------------------------------------------------------------------------------

def leds_test(ip_address, udp_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(8):
        sock.sendto(int(0x00).to_bytes(1, byteorder="big"), (ip_address, udp_port))
        time.sleep(0.2)
        sock.sendto(int(0xff).to_bytes(1, byteorder="big"), (ip_address, udp_port))
        time.sleep(0.2)


# Switches Test ------------------------------------------------------------------------------------

def switches_test(udp_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", udp_port))
    while True:
        data, addr = sock.recvfrom(1024)
        switches   = int.from_bytes(data, byteorder="big")
        print(f"Switches value: 0x{switches:02x}")

# Run ----------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth UDP Streamer test utility")
    parser.add_argument("--ip-address", default="192.168.1.50", help="Board's IP Address")
    parser.add_argument("--udp-port",   default="6000",         help="UDP Port")
    parser.add_argument("--leds",       action="store_true",    help="Test Leds over UDP Streamer")
    parser.add_argument("--switches",   action="store_true",    help="Test Switches over UDP Streamer")
    args = parser.parse_args()

    udp_port = int(args.udp_port, 0)

    if args.leds:
        leds_test(ip_address=args.ip_address, udp_port=udp_port)

    if args.switches:
        switches_test(udp_port=udp_port)

if __name__ == "__main__":
    main()
