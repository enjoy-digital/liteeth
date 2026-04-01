#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# LiteEth PTP test/monitor utility.

import argparse
import csv
import os
import sys
import time

from litex import RemoteClient

# Constants ----------------------------------------------------------------------------------------

HEADER_INTERVAL = 16  # Reprint column headers every N rows.

# Helpers ------------------------------------------------------------------------------------------

def to_signed(value, bits):
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value

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

    def _reg(self, suffix):
        return getattr(self.bus.regs, f"{self.name}_{suffix}")

    def _read(self, suffix):
        try:
            return self._reg(f"monitor_{suffix}").read()
        except AttributeError:
            return 0

    def _read_signed(self, suffix, bits=64):
        return to_signed(self._read(suffix), bits)

    def _read_ts80(self, suffix):
        ts = self._read(suffix)
        return ts >> 32, ts & 0xFFFFFFFF

    def snapshot(self):
        self._reg("monitor_snapshot").write(1)

        # Common CSRs.
        locked          = self._read("locked")
        master_ip       = self._read("master_ip")
        tsu_seconds     = self._read("tsu_seconds")
        tsu_nanoseconds = self._read("tsu_nanoseconds")
        addend          = self._read("addend")
        addend_frac     = self._read("addend_frac")
        phase           = self._read_signed("phase")
        delay           = self._read_signed("delay")
        serve_count     = self._read("serve_count")
        step_count      = self._read("step_count")
        reject_count    = self._read("reject_count")

        # Servo CSRs.
        last_sample_valid     = self._read("last_sample_valid")
        serve_tsu_seconds     = self._read("serve_tsu_seconds")
        serve_tsu_nanoseconds = self._read("serve_tsu_nanoseconds")
        serve_flags           = self._read("serve_flags")
        serve_offset          = self._read_signed("serve_offset")
        serve_addend_next     = self._read("serve_addend_next")
        serve_freq_step       = self._read_signed("serve_freq_step", 32)

        # Debug CSRs (optional).
        shadow_addend = self._read("shadow_addend") or addend
        shadow_frac   = self._read("shadow_frac")   or addend_frac
        offset        = self._read_signed("offset")

        t1_seconds, t1_nanoseconds = self._read_ts80("t1")
        t2_seconds, t2_nanoseconds = self._read_ts80("t2")
        t3_seconds, t3_nanoseconds = self._read_ts80("t3")
        t4_seconds, t4_nanoseconds = self._read_ts80("t4")

        live_sample_valid                      = self._read("live_sample_valid")
        live_t1_seconds, live_t1_nanoseconds   = self._read_ts80("live_t1")
        live_t2_seconds, live_t2_nanoseconds   = self._read_ts80("live_t2")
        live_t3_seconds, live_t3_nanoseconds   = self._read_ts80("live_t3")
        live_t4_seconds, live_t4_nanoseconds   = self._read_ts80("live_t4")
        live_dt21  = self._read_signed("live_dt21")
        live_dt43  = self._read_signed("live_dt43")
        live_phase = self._read_signed("live_phase")
        live_delay = self._read_signed("live_delay")

        # Derived values.
        tsu_ns = tsu_seconds * 1_000_000_000 + tsu_nanoseconds
        t1_ns  = t1_seconds  * 1_000_000_000 + t1_nanoseconds

        def delta_ns(a_sec, a_ns, b_sec, b_ns):
            if a_sec == b_sec:
                return a_ns - b_ns
            if a_sec == (b_sec + 1):
                return (a_ns + 1_000_000_000) - b_ns
            if b_sec == (a_sec + 1):
                return -((b_ns + 1_000_000_000) - a_ns)
            return 0

        has_debug = t1_seconds != 0 or offset != 0

        return {
            # Common.
            "locked":            locked,
            "master_ip":         master_ip,
            "master_ip_str":     ipv4_to_str(master_ip),
            "tsu_seconds":       tsu_seconds,
            "tsu_nanoseconds":   tsu_nanoseconds,
            "tsu_time":          tsu_seconds + tsu_nanoseconds / 1e9,
            "addend":            addend,
            "addend_frac":       addend_frac,
            "e2e_phase_ns":      phase,
            "e2e_delay_ns":      delay,
            "serve_count":       serve_count,
            "step_count":        step_count,
            "reject_count":      reject_count,
            # Servo.
            "last_sample_valid":     last_sample_valid,
            "serve_tsu_seconds":     serve_tsu_seconds,
            "serve_tsu_nanoseconds": serve_tsu_nanoseconds,
            "serve_flags":           serve_flags,
            "serve_valid":           (serve_flags >> 0) & 1,
            "serve_outlier":         (serve_flags >> 1) & 1,
            "serve_sec_adj":         (serve_flags >> 2) & 1,
            "serve_coarse":          (serve_flags >> 3) & 1,
            "serve_offset":          serve_offset,
            "serve_addend_next":     serve_addend_next,
            "serve_freq_step":       serve_freq_step,
            # Debug.
            "has_debug":         has_debug,
            "shadow_addend":     shadow_addend,
            "shadow_frac":       shadow_frac,
            "offset_ns":         offset,
            "t1_seconds":        t1_seconds,
            "t1_nanoseconds":    t1_nanoseconds,
            "t1_time":           t1_seconds + t1_nanoseconds / 1e9,
            "t2_seconds":        t2_seconds,
            "t2_nanoseconds":    t2_nanoseconds,
            "t3_seconds":        t3_seconds,
            "t3_nanoseconds":    t3_nanoseconds,
            "t4_seconds":        t4_seconds,
            "t4_nanoseconds":    t4_nanoseconds,
            "live_t1_seconds":       live_t1_seconds,
            "live_t1_nanoseconds":   live_t1_nanoseconds,
            "live_t2_seconds":       live_t2_seconds,
            "live_t2_nanoseconds":   live_t2_nanoseconds,
            "live_t3_seconds":       live_t3_seconds,
            "live_t3_nanoseconds":   live_t3_nanoseconds,
            "live_t4_seconds":       live_t4_seconds,
            "live_t4_nanoseconds":   live_t4_nanoseconds,
            "live_sample_valid":     live_sample_valid,
            "tsu_minus_t1_ns":       tsu_ns - t1_ns,
            "t2_minus_t1_ns":        delta_ns(t2_seconds, t2_nanoseconds, t1_seconds, t1_nanoseconds),
            "t4_minus_t3_ns":        delta_ns(t4_seconds, t4_nanoseconds, t3_seconds, t3_nanoseconds),
            "live_t2_minus_t1_ns":   live_dt21,
            "live_t4_minus_t3_ns":   live_dt43,
            "live_e2e_phase_ns":     live_phase,
            "live_e2e_delay_ns":     live_delay,
        }

# Display ------------------------------------------------------------------------------------------

def print_header(debug=False):
    C = Colors
    hdr = (
        f"{C.DIM}"
        f"{'#':>4s} {'Lock':>4s} {'Master IP':>15s} "
        f"{'Phase (ns)':>12s} {'Delay (ns)':>10s} "
        f"{'Addend':>10s} "
        f"{'Serve':>6s} {'Step':>5s} {'Rej':>5s} {'Flags':>4s}"
    )
    if debug:
        hdr += (
            f" {'Offset (ns)':>12s}"
            f" {'dt21 (ns)':>12s} {'dt43 (ns)':>12s}"
            f" {'Freq Step':>10s}"
        )
    hdr += f"{C.RESET}"
    print(hdr)

def print_sample(index, s, debug=False):
    C = Colors

    # Lock status (fixed 4-char visible width).
    if s["locked"]:
        lock_str = _color(" YES", C.GREEN)
    else:
        lock_str = _color("  NO", C.RED)

    # Phase coloring.
    phase = s["e2e_phase_ns"]
    phase_raw = f"{phase:>12d}"
    abs_phase = abs(phase)
    if abs_phase < 100:
        phase_str = _color(phase_raw, C.GREEN)
    elif abs_phase < 1000:
        phase_str = _color(phase_raw, C.YELLOW)
    else:
        phase_str = _color(phase_raw, C.RED)

    # Addend (fixed 10-char visible width: 0xNN.NNNNN).
    addend_str = f"0x{s['addend']:02x}.{s['addend_frac']:05x}"

    # Servo flags (fixed 4-char visible width).
    flags = ""
    flags += _color("V", C.GREEN)  if s["serve_valid"]   else _color(".", C.DIM)
    flags += _color("O", C.RED)    if s["serve_outlier"] else _color(".", C.DIM)
    flags += _color("S", C.YELLOW) if s["serve_sec_adj"] else _color(".", C.DIM)
    flags += _color("C", C.YELLOW) if s["serve_coarse"]  else _color(".", C.DIM)

    line = (
        f"{index:4d} {lock_str} {s['master_ip_str']:>15s} "
        f"{phase_str} {s['e2e_delay_ns']:>10d} "
        f"{addend_str} "
        f"{s['serve_count']:>6d} {s['step_count']:>5d} {s['reject_count']:>5d} {flags}"
    )

    # Debug fields.
    if debug and s["has_debug"]:
        offset = s["offset_ns"]
        offset_raw = f"{offset:>12d}"
        abs_offset = abs(offset)
        if abs_offset < 100:
            offset_str = _color(offset_raw, C.GREEN)
        elif abs_offset < 1000:
            offset_str = _color(offset_raw, C.YELLOW)
        else:
            offset_str = _color(offset_raw, C.RED)

        line += (
            f" {offset_str}"
            f" {s['t2_minus_t1_ns']:>12d} {s['t4_minus_t3_ns']:>12d}"
            f" {s['serve_freq_step']:>10d}"
        )

    print(line)

def print_summary(samples):
    C = Colors
    phase_vals = [s["e2e_phase_ns"] for s in samples]
    delay_vals = [s["e2e_delay_ns"] for s in samples]

    if not phase_vals:
        return

    min_phase = min(phase_vals)
    max_phase = max(phase_vals)
    avg_phase = sum(phase_vals) / len(phase_vals)
    min_delay = min(delay_vals)
    max_delay = max(delay_vals)
    avg_delay = sum(delay_vals) / len(delay_vals)

    last = samples[-1]

    print()
    print(f"{C.CYAN}--- Summary ({len(samples)} samples) ---{C.RESET}")
    print(f"  Phase  (ns) : min={min_phase:>10d}  max={max_phase:>10d}  avg={avg_phase:>10.0f}")
    print(f"  Delay  (ns) : min={min_delay:>10d}  max={max_delay:>10d}  avg={avg_delay:>10.0f}")
    print(f"  Counters    : serve={last['serve_count']}  step={last['step_count']}  reject={last['reject_count']}")
    print(f"  Addend      : 0x{last['addend']:02x}.{last['addend_frac']:05x}")
    if last["locked"]:
        print(f"  Status      : {C.GREEN}LOCKED{C.RESET} to {last['master_ip_str']}")
    else:
        print(f"  Status      : {C.RED}UNLOCKED{C.RESET}")

# PTP Monitor Test ---------------------------------------------------------------------------------

def ptp_test(name="ptp", interval=0.5, count=20, debug=False, csv_out=None, plot=False):
    bus = RemoteClient(csr_csv="csr.csv")
    bus.open()

    # PTP Driver.
    driver = PTPDriver(bus=bus, name=name)

    # Capture Loop.
    samples = []
    try:
        for i in range(1, count + 1):
            if ((i - 1) % HEADER_INTERVAL) == 0:
                print_header(debug=debug)

            s = driver.snapshot()
            samples.append(s)
            print_sample(i, s, debug=debug)

            if i != count:
                time.sleep(interval)
    except KeyboardInterrupt:
        print()
    finally:
        bus.close()

    # Summary.
    if samples:
        print_summary(samples)

    # CSV Export (Optional).
    if csv_out and samples:
        fieldnames = [
            "index", "locked", "master_ip", "master_ip_str",
            "tsu_seconds", "tsu_nanoseconds", "t1_seconds", "t1_nanoseconds",
            "t2_seconds", "t2_nanoseconds", "t3_seconds", "t3_nanoseconds",
            "t4_seconds", "t4_nanoseconds", "tsu_minus_t1_ns",
            "t2_minus_t1_ns", "t4_minus_t3_ns", "e2e_phase_ns", "e2e_delay_ns",
            "offset_ns", "addend", "addend_frac", "serve_count", "step_count",
            "reject_count", "last_sample_valid", "live_sample_valid",
            "live_t2_minus_t1_ns", "live_t4_minus_t3_ns", "live_e2e_phase_ns", "live_e2e_delay_ns",
        ]
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for index, sample in enumerate(samples, start=1):
                row = {"index": index}
                row.update({k: sample[k] for k in fieldnames if k != "index"})
                writer.writerow(row)
        print(f"Wrote {len(samples)} samples to {csv_out}")

    # Plot (Optional).
    if plot and samples:
        import matplotlib.pyplot as plt

        xs       = list(range(1, len(samples) + 1))
        phase_ns = [s["e2e_phase_ns"] for s in samples]
        addend   = [s["addend"]       for s in samples]
        has_debug = any(s["has_debug"] for s in samples)

        if has_debug:
            tsu_offset_us = [(s["tsu_time"] - s["t1_time"]) * 1e6 for s in samples]
            age_ns        = [s["tsu_minus_t1_ns"] for s in samples]

            fig, axes = plt.subplots(4, 1, sharex=True, figsize=(12, 10))
            axes[0].plot(xs, phase_ns, marker="o", markersize=3)
            axes[0].set_ylabel("Phase Error (ns)")
            axes[0].grid(True)
            axes[1].plot(xs, tsu_offset_us, marker="o", markersize=3, color="green")
            axes[1].set_ylabel("TSU - Master T1 (us)")
            axes[1].grid(True)
            axes[2].plot(xs, age_ns, marker="o", markersize=3, color="orange")
            axes[2].set_ylabel("TSU - Last T1 (ns)")
            axes[2].grid(True)
            axes[3].plot(xs, addend, marker="o", markersize=3, color="red")
            axes[3].set_ylabel("Addend")
            axes[3].set_xlabel("Sample")
            axes[3].grid(True)
        else:
            fig, axes = plt.subplots(2, 1, sharex=True, figsize=(12, 6))
            axes[0].plot(xs, phase_ns, marker="o", markersize=3)
            axes[0].set_ylabel("Phase Error (ns)")
            axes[0].grid(True)
            axes[1].plot(xs, addend, marker="o", markersize=3, color="red")
            axes[1].set_ylabel("Addend")
            axes[1].set_xlabel("Sample")
            axes[1].grid(True)

        fig.suptitle("LiteEth PTP Monitor")
        plt.tight_layout()
        plt.show()

# Main ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteEth PTP test/monitor utility.")
    parser.add_argument("--name",     default="ptp",  help="CSR prefix name.")
    parser.add_argument("--interval", default=0.5,  type=float, help="Polling interval in seconds.")
    parser.add_argument("--count",    default=20,   type=int,   help="Number of samples.")
    parser.add_argument("--debug",    action="store_true", help="Show debug fields (timestamps, offsets).")
    parser.add_argument("--csv-out",  default=None, help="Optional CSV output path.")
    parser.add_argument("--plot",     action="store_true", help="Plot phase/addend after capture.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output.")
    args = parser.parse_args()

    # Disable colors if requested or not a TTY.
    if args.no_color or not sys.stdout.isatty():
        Colors.disable()

    ptp_test(
        name     = args.name,
        interval = args.interval,
        count    = args.count,
        debug    = args.debug,
        csv_out  = args.csv_out,
        plot     = args.plot,
    )

if __name__ == "__main__":
    main()
