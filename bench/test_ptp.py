#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# LiteEth PTP test/monitor utility (lock status and master IP from the PTP core CSRs).

import argparse
import csv
import os
import sys
import time

from litex import RemoteClient

# Constants ----------------------------------------------------------------------------------------

HEADER_INTERVAL = 16  # Reprint column headers every N rows.

# Helpers ------------------------------------------------------------------------------------------

def ipv4_to_str(value):
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))

# Colors -------------------------------------------------------------------------------------------

class Colors:
    GREEN  = "\033[32m"
    RED    = "\033[31m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    @classmethod
    def disable(cls):
        cls.GREEN  = ""
        cls.RED    = ""
        cls.YELLOW = ""
        cls.CYAN   = ""
        cls.DIM    = ""
        cls.RESET  = ""

def _color(text, color):
    """Wrap text in color codes (no-op when colors are disabled)."""
    return f"{color}{text}{Colors.RESET}" if color else text

# PTP Driver ---------------------------------------------------------------------------------------

class PTPDriver:
    def __init__(self, bus, name="ptp"):
        self.bus  = bus
        self.name = name

    def _read(self, suffix):
        return getattr(self.bus.regs, f"{self.name}_{suffix}").read()

    def snapshot(self):
        locked    = self._read("locked")
        master_ip = self._read("master_ip")
        return {
            "locked":        locked,
            "master_ip":     master_ip,
            "master_ip_str": ipv4_to_str(master_ip),
        }

# Display ------------------------------------------------------------------------------------------

def print_header():
    C = Colors
    print(f"{C.DIM}{'#':>4s} {'Lock':>4s} {'Master IP':>15s}{C.RESET}")

def print_sample(index, s):
    C = Colors

    # Lock status (fixed 4-char visible width).
    if s["locked"]:
        lock_str = _color(" YES", C.GREEN)
    else:
        lock_str = _color("  NO", C.RED)

    print(f"{index:4d} {lock_str} {s['master_ip_str']:>15s}")

def print_summary(samples):
    C = Colors
    if not samples:
        return

    locked_count = sum(s["locked"] for s in samples)
    lock_changes = sum(a["locked"] != b["locked"] for a, b in zip(samples, samples[1:]))
    last         = samples[-1]

    print()
    print(f"{C.CYAN}--- Summary ({len(samples)} samples) ---{C.RESET}")
    print(f"  Locked      : {locked_count}/{len(samples)} samples, {lock_changes} lock change(s)")
    if last["locked"]:
        print(f"  Status      : {C.GREEN}LOCKED{C.RESET} to {last['master_ip_str']}")
    else:
        print(f"  Status      : {C.RED}UNLOCKED{C.RESET}")

# PTP Monitor Test ---------------------------------------------------------------------------------

def ptp_test(name="ptp", interval=0.5, count=20, csv_out=None):
    bus = RemoteClient(csr_csv="csr.csv")
    bus.open()

    # PTP Driver.
    driver = PTPDriver(bus=bus, name=name)

    # Capture Loop.
    samples = []
    try:
        for i in range(1, count + 1):
            if ((i - 1) % HEADER_INTERVAL) == 0:
                print_header()

            s = driver.snapshot()
            samples.append(s)
            print_sample(i, s)

            if i != count:
                time.sleep(interval)
    except KeyboardInterrupt:
        print()
    finally:
        bus.close()

    # Summary.
    print_summary(samples)

    # CSV Export (Optional).
    if csv_out and samples:
        fieldnames = ["index", "locked", "master_ip", "master_ip_str"]
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for index, sample in enumerate(samples, start=1):
                writer.writerow({"index": index, **sample})
        print(f"Wrote {len(samples)} samples to {csv_out}")

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth PTP test/monitor utility.")
    parser.add_argument("--name",     default="ptp", help="CSR prefix name.")
    parser.add_argument("--interval", default=0.5,   type=float, help="Polling interval in seconds.")
    parser.add_argument("--count",    default=20,    type=int,   help="Number of samples.")
    parser.add_argument("--csv-out",  default=None,  help="Optional CSV output path.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output.")
    args = parser.parse_args()

    # Disable colors if requested or not a TTY.
    if args.no_color or not sys.stdout.isatty():
        Colors.disable()

    ptp_test(
        name     = args.name,
        interval = args.interval,
        count    = args.count,
        csv_out  = args.csv_out,
    )

if __name__ == "__main__":
    main()
