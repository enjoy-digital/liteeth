#!/usr/bin/env python3

#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""LiteEth 1000BASE-X / SGMII PCS link monitor.

Polls the PCS observability CSRs over Etherbone, prints a live
table of link state / AN phase / sticky bits / restart counter, and
emits a summary (uptime fraction, restart rate, time-to-link, state
distribution). Optionally writes raw samples to CSV.

Typical use, against a live SoC with `csr.csv` in the current dir:

  ./pcs_link_monitor.py                            # 1 minute, 0.5s sample
  ./pcs_link_monitor.py --interval 1 --count 600   # 10 minutes
  ./pcs_link_monitor.py --csv-out direct.csv       # log for diff vs adapter
  ./pcs_link_monitor.py --clear                    # re-arm sticky and exit

If the PCS lives under a non-default CSR namespace (e.g. multiple
PHYs in the SoC), pass --prefix.
"""

import argparse
import collections
import csv
import sys
import time

from litex import RemoteClient

# AN FSM state encoding. Must match an_state in liteeth/phy/pcs_1000basex.py.
AN_STATES = {
    0: "BREAKLINK",
    1: "WAIT-ABI",
    2: "WAIT-ACK",
    3: "SEND-MORE-ACK",
    4: "IDLE-DETECT",
    5: "RUNNING",
}

# ANSI colors -------------------------------------------------------------------------------------

class C:
    GREEN  = "\033[32m"
    RED    = "\033[31m"
    YELLOW = "\033[33m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    @classmethod
    def disable(cls):
        for k in ("GREEN", "RED", "YELLOW", "DIM", "RESET"):
            setattr(cls, k, "")


# Driver ------------------------------------------------------------------------------------------

class PCSDriver:
    def __init__(self, bus, prefix="ethphy_pcs"):
        self.bus    = bus
        self.prefix = prefix
        # Validate that all required CSRs exist; fail early with a helpful
        # message if the prefix is wrong.
        for name in ("status", "debug", "restart_count", "debug_clear"):
            if not hasattr(self.bus.regs, f"{self.prefix}_{name}"):
                raise SystemExit(
                    f"CSR {self.prefix}_{name} not found.\n"
                    f"  - Is the SoC actually built with PCS observability "
                    f"(this is the default in all wrapper PHYs but only when "
                    f"`with_csr=True` is in effect)?\n"
                    f"  - Is your --prefix correct? "
                    f"Inspect csr.csv to see the actual PCS register names."
                )

    def _reg(self, name):
        return getattr(self.bus.regs, f"{self.prefix}_{name}")

    def status(self):
        v = self._reg("status").read()
        return {
            "link_up":    (v >>  0) & 1,
            "is_sgmii":   (v >>  1) & 1,
            "link_rf":    (v >>  2) & 1,
            "config_reg": (v >> 16) & 0xffff,
        }

    def debug(self):
        v = self._reg("debug").read()
        return {
            "an_state":        (v >> 0) & 0xf,
            "seen_valid_ci":   (v >> 4) & 1,
            "seen_config_abi": (v >> 5) & 1,
            "seen_config_ack": (v >> 6) & 1,
            "rx_invalid":      (v >> 7) & 1,
        }

    def restart_count(self):
        return self._reg("restart_count").read()

    def clear_sticky(self):
        self._reg("debug_clear").write(1)


# Pretty printing ---------------------------------------------------------------------------------

HEADER = (
    f"{'time':>7s}  {'link':>4s}  sgmii  rf  {'state':<14s}  "
    f"{'restarts':>8s}  {'+r':>3s}  ci  abi  ack  inv  config"
)
SEP = "-" * len(HEADER)


def fmt_state(name):
    if name == "RUNNING":
        return f"{C.GREEN}{name:<14s}{C.RESET}"
    if name == "BREAKLINK":
        return f"{C.DIM}{name:<14s}{C.RESET}"
    if name in ("IDLE-DETECT",):
        return f"{C.YELLOW}{name:<14s}{C.RESET}"
    return f"{name:<14s}"


def fmt_link(v):
    return f"{C.GREEN}UP  {C.RESET}" if v else f"{C.RED}DOWN{C.RESET}"


def fmt_bit(v):
    return f"{C.GREEN}1{C.RESET}" if v else f"{C.DIM}0{C.RESET}"


def fmt_restart_delta(d):
    return f"{C.RED}{d:>3d}{C.RESET}" if d else f"{C.DIM}{d:>3d}{C.RESET}"


def fmt_rf(v):
    # RF being set is a useful diagnostic (peer is signalling fault),
    # so highlight it; absence is the normal case so dim it.
    return f"{C.RED}1{C.RESET}" if v else f"{C.DIM}0{C.RESET}"


def print_sample(t, sample, delta_restart):
    s = sample
    state = AN_STATES.get(s["an_state"], f"?{s['an_state']}")
    line = (
        f"{t:7.2f}  "
        f"{fmt_link(s['link_up'])}  "
        f"  {s['is_sgmii']:d}    "
        f"{fmt_rf(s['link_rf'])}   "
        f"{fmt_state(state)}  "
        f"{s['restart_count']:>8d}  "
        f"{fmt_restart_delta(delta_restart)}  "
        f"{fmt_bit(s['seen_valid_ci'])}   "
        f"{fmt_bit(s['seen_config_abi'])}    "
        f"{fmt_bit(s['seen_config_ack'])}    "
        f"{fmt_bit(s['rx_invalid'])}  "
        f"0x{s['config_reg']:04x}"
    )
    print(line)


# Monitor loop ------------------------------------------------------------------------------------

def monitor(bus, prefix, interval, count, csv_path=None, header_every=20,
            keep_sticky=False):
    drv = PCSDriver(bus, prefix=prefix)

    if not keep_sticky:
        drv.clear_sticky()

    rows               = []
    state_samples      = collections.Counter()
    first_link_up_t    = None
    last_restart_count = drv.restart_count()
    initial_restart    = last_restart_count

    t0 = time.monotonic()
    samples_with_link  = 0

    print(HEADER)
    print(SEP)

    try:
        for i in range(count):
            t = time.monotonic() - t0
            s = drv.status()
            d = drv.debug()
            r = drv.restart_count()

            sample = {**s, **d, "restart_count": r}
            state_name = AN_STATES.get(d["an_state"], f"?{d['an_state']}")
            state_samples[state_name] += 1
            new_restarts = r - last_restart_count
            last_restart_count = r

            if s["link_up"]:
                samples_with_link += 1
                if first_link_up_t is None:
                    first_link_up_t = t

            if i and (i % header_every) == 0:
                print(SEP)
                print(HEADER)
                print(SEP)
            print_sample(t, sample, new_restarts)

            rows.append({"time_s": t, "delta_restart": new_restarts, **sample})

            # Sleep until next sample, accounting for the read cost.
            sleep_for = interval - ((time.monotonic() - t0) - i * interval)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print()

    total      = time.monotonic() - t0
    n_samples  = len(rows)
    if n_samples == 0:
        return

    total_restarts = last_restart_count - initial_restart

    print()
    print(SEP)
    print(f"Samples           : {n_samples}")
    print(f"Wall time         : {total:7.2f} s")
    print(f"Restarts observed : {total_restarts}")
    print(f"Restart rate      : {total_restarts / total * 60:7.2f} / min")
    if first_link_up_t is not None:
        link_pct = 100.0 * samples_with_link / n_samples
        print(f"Time to first UP  : {first_link_up_t:7.2f} s")
        print(f"Link UP fraction  : {link_pct:7.2f} %")
    else:
        print(f"Time to first UP  : {C.RED}NEVER{C.RESET}")
        print(f"Link UP fraction  : {C.RED}0.00 %{C.RESET}")

    print()
    print("State distribution:")
    for state, n in state_samples.most_common():
        pct = 100.0 * n / n_samples
        bar = "#" * int(pct / 2)
        print(f"  {state:<14s} {n:>4d}  {pct:5.1f} %  {bar}")

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            fieldnames = ["time_s", "delta_restart"] + list(rows[0].keys() - {"time_s", "delta_restart"})
            # Stable column order:
            fieldnames = ["time_s", "link_up", "is_sgmii", "link_rf",
                          "an_state",
                          "seen_valid_ci", "seen_config_abi", "seen_config_ack",
                          "rx_invalid", "config_reg", "restart_count",
                          "delta_restart"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nSamples written to {csv_path}")


# CLI ---------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LiteEth 1000BASE-X / SGMII PCS link monitor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--prefix",       default="ethphy_pcs",
        help="CSR namespace prefix (default: ethphy_pcs)")
    parser.add_argument("--csr-csv",      default="csr.csv",
        help="CSR CSV file (default: csr.csv)")
    parser.add_argument("--interval",     type=float, default=0.5,
        help="Sample interval in seconds (default: 0.5)")
    parser.add_argument("--count",        type=int, default=120,
        help="Number of samples (default: 120)")
    parser.add_argument("--csv-out",      default=None,
        help="Write all samples to this CSV file")
    parser.add_argument("--keep-sticky",  action="store_true",
        help="Do NOT clear sticky bits before sampling (carry over from a previous run)")
    parser.add_argument("--clear",        action="store_true",
        help="Clear sticky bits + restart counter and exit")
    parser.add_argument("--no-color",     action="store_true",
        help="Disable ANSI colors")
    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        C.disable()

    bus = RemoteClient(csr_csv=args.csr_csv)
    bus.open()
    try:
        if args.clear:
            PCSDriver(bus, prefix=args.prefix).clear_sticky()
            print("Cleared sticky observability fields and restart counter.")
            return
        monitor(
            bus,
            prefix      = args.prefix,
            interval    = args.interval,
            count       = args.count,
            csv_path    = args.csv_out,
            keep_sticky = args.keep_sticky,
        )
    finally:
        bus.close()


if __name__ == "__main__":
    main()
