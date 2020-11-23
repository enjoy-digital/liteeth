#!/usr/bin/env python3

#
# This file is part of LiteEth
#
# Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# LiteEth Etherbone test utility.

import sys
import time
import argparse

from litex import RemoteClient

KiB = 1024
MiB = 1024*KiB

# Identifier Test ----------------------------------------------------------------------------------

def ident_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    fpga_identifier = ""

    for i in range(256):
        c = chr(wb.read(wb.bases.identifier_mem + 4*i) & 0xff)
        fpga_identifier += c
        if c == "\0":
            break

    print(fpga_identifier)

    wb.close()

# Access Test --------------------------------------------------------------------------------------

def access_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    data = 0x12345678
    addr = 0x100

    print("Write over Etherbone at 0x{:08x}: 0x{:08x}.".format(addr, data))
    wb.write(wb.mems.sram.base + addr, data)
    print("Read  over Etherbone at 0x{:08x}: 0x{:08x}.".format(addr, wb.read(wb.mems.sram.base + addr)))

    wb.close()

# SRAM Test ----------------------------------------------------------------------------------------

def sram_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    def mem_dump(base, length):
        for addr in range(base, base + length, 4):
            if (addr%16 == 0):
                if addr != base:
                    print("")
                print("0x{:08x}".format(addr), end="  ")
            data = wb.read(addr)
            for i in reversed(range(4)):
                print("{:02x}".format((data >> (8*i)) & 0xff), end=" ")
        print("")

    def mem_write(base, datas):
        for n, addr in enumerate(range(base, base + 4*len(datas), 4)):
            if (addr%16 == 0):
                if addr != base:
                    print("")
                print("0x{:08x}".format(addr), end="  ")
            data = datas[n]
            for i in reversed(range(4)):
                print("{:02x}".format((data >> (8*i)) & 0xff), end=" ")
            wb.write(addr, data)
        print("")


    print("Fill SRAM with counter:")
    mem_write(wb.mems.sram.base, [i for i in range(128//4)])
    print("")

    print("Dump SRAM:")
    mem_dump(wb.mems.sram.base, 128)
    print("")

    print("Fill SRAM with 4 32-bit words:")
    mem_write(wb.mems.sram.base, [0x01234567, 0x89abcdef, 0x5aa55aa5, 0xa55aa55a])
    print("")

    print("Dump SRAM:")
    mem_dump(wb.mems.sram.base, 128)
    print("")

    wb.close()

# Speed Test ---------------------------------------------------------------------------------------

def speed_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    test_size = 16*KiB

    print("Testing write speed... ", end="")
    start = time.time()
    for i in range(test_size//4):
        wb.write(wb.mems.sram.base + i%0x100, i)
    end = time.time()
    duration = (end - start)
    print("{:3.2f} KiB/s".format(test_size/(duration*KiB)))

    print("Testing read speed... ", end="")
    start = time.time()
    for i in range(test_size//4):
        wb.read(wb.mems.sram.base + i%0x100)
    end = time.time()
    duration = (end - start)
    print("{:3.2f} KiB/s".format(test_size/(duration*KiB)))

    wb.close()

# Run ----------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth Etherbone test utility")
    parser.add_argument("--port",   default="1234",      help="Host bind port")
    parser.add_argument("--ident",  action="store_true", help="Read FPGA identifier")
    parser.add_argument("--access", action="store_true", help="Test single Write/Read access over Etherbone")
    parser.add_argument("--sram",   action="store_true", help="Test SRAM access over Etherbone")
    parser.add_argument("--speed",  action="store_true", help="Test speed over Etherbone")
    args = parser.parse_args()

    port = int(args.port, 0)

    if args.ident:
        ident_test(port=port)

    if args.access:
        access_test(port=port)

    if args.sram:
        sram_test(port=port)

    if args.speed:
        speed_test(port=port)

if __name__ == "__main__":
    main()
