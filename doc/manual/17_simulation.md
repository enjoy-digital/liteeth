# Simulation and verification {#simulation}

This chapter describes how LiteEth is verified without a board: the unit tests and their Python
models, the generated-Verilog tests, the continuous integration, and the full-system simulations
that put a simulated SoC on the network of the host. Read it before changing the core or a PHY,
and to reproduce a problem seen on a board.

## Verification strategy {#sim-strategy}

Most of LiteEth is protocol logic: framing, checksums, parsing, packet buffering. That logic is
tested in simulation against Python models of the other end of the link, with no FPGA tool. The
PHYs add vendor primitives, which simulation can only elaborate: their analog behavior, timing
closure and interoperability need a board.

| Level | What it checks | Tool | Where |
|----------------------------|-----------------------------------------|--------------------|----------------------------|
| Unit tests | One block or the whole core against models | Migen simulator | `test/` |
| Generated-Verilog tests | The Verilog that LiteX writes, bounded benches | Icarus, Verilator | `test/*_rtl.py` |
| Elaboration tests | PHY wrappers, primitives, generator outputs | Python only | `test/` |
| Full-system simulation | A SoC on the host network through a TAP | Verilator | `litex_sim`, `bench/` |
| Board | Link, traffic, resets, timing | FPGA toolchain | `bench/` targets |

## Python models {#sim-models}

The models of `test/model/` play the far end of the link, written in Python. Each model encodes
and decodes one layer: `phy`, `mac`, `arp`, `ip`, `icmp`, `udp` and `etherbone`. The PHY model is
a stream source and sink of the width of the PHY; the core under test uses it as its PHY. The
figure below shows a UDP test.

![A unit test of the core. The Python streamer sends packets to a UDP user port of the core; the
core sends frames to the PHY model, the models decode them up to the UDP layer and loop the
payload back; the logger checks what returns.](sim_models.svg)

The models answer like a remote host: the ARP model answers the requests of the core, the UDP
model sends each packet back (`loopback=True`), and the MAC model checks the preamble and the CRC
of each frame. A
test compares the logged packets with the sent ones, so a single test covers both directions of
every layer. `test/model/dumps.py` holds captured frames (ARP, UDP, ping) that tests decode and
compare field by field.

`test/stream_helpers.py` provides the streamer, the logger and a randomizer that inserts idle
cycles and backpressure. `test/model/pcs_100g.py` is a block-level reference of the 100GBASE-R
PCS, for the vectors of the 100G work; it is not synthesizable.

## Unit and system tests {#sim-tests}

The suite holds about 70 test files and about 495 test functions. They use `unittest` and run
under `pytest`; there are no markers, so `pytest` always runs the whole suite. The table groups
the files by area, with the number of test functions of the largest ones.

| Area | Files | Highlights |
|------------------------|--------------------------------------------------|-----------------------------------------------|
| MAC | `test_mac_*`, `test_crc`, `test_store_and_forward` | Packet, CDC, padding, gap, hybrid, bounds, AXI-Stream |
| Stack | `test_arp`, `test_ip*`, `test_icmp`, `test_udp*` | End to end with the models, IPv4 validation, gateway |
| DHCP, IGMP | `test_dhcp` (11), `test_igmp` (9) | Exchange, lease, membership reports |
| Frontends | `test_etherbone`, `test_stream` (21) | Records, streamer packetization, `be` |
| Contracts | `test_packet_*`, `test_byte_enable` | Stream rules of [Core architecture](#architecture) |
| Wide datapaths | `test_wide_datapath*` | 128 to 512-bit paths, checksums, MAC loopback |
| PTP | `test_ptp` (35), `test_ptp_servo` (89) | Messages, TSU, control, servo |
| BASE-X | `test_pcs_1000basex` (26), `test_basex_*` | PCS, autonegotiation, PMA, status |
| BASE-R | `test_pcs_baser` (40), `test_baser_*` | PCS, rates, PLLs, diagnostics, generator |
| Parallel PHYs | `test_rgmii_rx`, `test_ecp5rgmii`, `test_xgmii_phy` | RGMII errors, in-band status, deficit idle count |
| Other PHYs | `test_10baset`, `test_us_lvds_*`, `test_gw5_*` | 10BASE-T, LVDS, GW5, GateMate, Agilex |
| Generator | `test_gen` (11), `test_baser_gen` | Example elaboration, ports |
| 100G | `test_mac_100g`, `test_cmac_100g`, `test_pcs_100g_model` | Traffic, wrapper, PCS reference |

### Commands {#sim-commands}

```sh
# Whole suite, as the continuous integration runs it.
python3 -m pytest -v
# One file, or one test.
python3 -m pytest -v test/test_udp.py
python3 -m pytest -v test/test_pcs_baser.py -k bad_blocks
```

The generated-Verilog tests skip themselves when their simulator is missing: Icarus Verilog
(`iverilog`, `vvp`) for the packet path tests, Verilator 5 for the complete MAC and core tests and
for `test_mac_100g`. A run without them passes with these tests reported as skipped, so check the
skip count before trusting a local run.

### Generated-Verilog tests {#sim-rtl}

The files ending in `_rtl.py` simulate the Verilog that LiteX generates, not the Python
description: they catch conversion problems the Migen simulator cannot see.
`test/rtl_helpers.py` converts a module, writes a bounded test bench with an explicit pin mapping
and runs it (`run_rtl`). The full MAC and core loopback of `test_wide_datapath_rtl.py` keeps its
clock-domain crossing FIFOs but uses one clock for all domains, so it does not check asynchronous
clock ratios.

## Continuous integration {#sim-ci}

The workflow `.github/workflows/ci.yml` runs on every push and pull request, on Ubuntu 24.04 with
Python 3.9 and 3.12. It installs Icarus Verilog and Verilator, installs LiteX and its cores with
`litex_setup.py`, installs LiteEth and runs `python3 -m pytest -v`. The manual has its own
workflow, described in [Maintaining this manual](#maintenance).

The continuous integration does not run a vendor toolchain. Vivado builds, timing reports and
hardware tests are run by hand, and their results are recorded where a public source states them
([Performance and resources](#performance)).

## Full-system simulation {#sim-system}

A full-system simulation runs a complete SoC in Verilator and connects its PHY to the network
stack of the host. The LiteX simulation module `ethernet` creates a TAP interface, `tap0`, gives
it the host address, and moves frames between it and the PHY model. The host then talks to the
simulated SoC as to a board, as the figure below shows.

![Full-system simulation. The PHY model of the SoC exchanges a stream with the ethernet module of
the simulator, which forwards the frames to the tap0 interface of the host.](sim_tap.svg)

Creating a TAP interface needs administrator rights: run the simulation with `sudo`, or give the
process the right to configure network interfaces. With a user installation of LiteX, `sudo` resets
`PATH` and drops the user packages: keep the environment, as in
`sudo -E env PATH=$PATH litex_sim --with-ethernet`. The simulation runs far slower than hardware:
`bench/sim.py` and `bench/dhcp_tap.py` clock the SoC at a simulated 1 MHz.

### `litex_sim` {#sim-litex-sim}

The LiteX simulator builds a SoC with a CPU and the BIOS. With `--with-ethernet`, it adds the
software MAC; with `--with-etherbone`, the Etherbone core; with both, the hybrid mode. The FPGA
side uses 192.168.1.50 (`--local-ip`) and `tap0` the host address 192.168.1.100 (`--remote-ip`).
When both options are given, the Etherbone core takes the next address, 192.168.1.51.

```sh
# BIOS with the software MAC: ping the host from the BIOS, or netboot over TFTP.
sudo litex_sim --with-ethernet
# Etherbone only, reachable from the host.
sudo litex_sim --with-etherbone
```

`--ethernet-phy-model` selects the PHY model: `sim` (the stream model, default), `gmii` or
`xgmii`, each with its own simulation module. In the BIOS console, `ping 192.168.1.100` checks the
path to the host; [Getting started](#getting-started) describes the BIOS network commands.

### `bench/sim.py`: Etherbone {#sim-bench}

`bench/sim.py` builds the Etherbone design of the bench targets on the PHY model: no CPU, a 4 KiB
SRAM and the default 192.168.1.50 address. Once it runs, the host tools of
[Examples and targets](#examples) work unchanged.

```sh
sudo ./bench/sim.py
# In another terminal, from the same directory (csr.csv).
ping 192.168.1.50
litex_server --udp --udp-ip 192.168.1.50 &
./bench/test_etherbone.py --ident --sram
```

### `bench/dhcp_tap.py`: DHCP client {#sim-dhcp}

`bench/dhcp_tap.py` simulates the DHCP client of the core against a DHCP server. By default, the
script also runs a small server on `tap0` (a raw socket, so Linux only): it offers 192.168.1.50
from 192.168.1.1 to the client MAC address 10:e2:d5:00:00:01. The simulation prints `DHCP_START`,
then `DHCP_DONE` with the leased address, or `DHCP_TIMEOUT`, and stops.

| Option | Default | Effect |
|------------------------|----------------------|----------------------------------------------------|
| `--interface` | `tap0` | TAP interface of the simulation module |
| `--offered-ip` | 192.168.1.50 | Address that the emulated server offers |
| `--server-ip` | 192.168.1.1 | Address of the emulated server and router |
| `--timeout` | 0.25 | Timeout of the DHCP core, in simulated seconds |
| `--no-server` | off | Simulation only, with an external DHCP server |
| `--server-only` | off | Emulated server only |
| `--no-etherbone` | off | No Etherbone on the core |

The simulation stops as soon as the client has an address or gives up, so it is a check of the
exchange, not a running target.

### `bench/mac_100g.py`: 100G traffic {#sim-100g}

`bench/mac_100g.py` runs traffic through the UDP/IP core and the AXI-Stream adapter of the 100G
path in Verilator, with independent clocks and finite queues. Python builds the reference
packets; the test bench checks the bytes, the metadata, the byte masks, the order and the losses,
and writes its results as JSON: packet rates, wire and payload rates, losses, errors and maximum
latency.

```sh
PYTHONPATH=. python3 bench/mac_100g.py --profile standard --packets 128
PYTHONPATH=. python3 bench/mac_100g.py --profile minimum --packets 128
PYTHONPATH=. python3 bench/mac_100g.py --profile mixed --packets 64 \
    --stall-cycles 200 --error-every 13 --reset
```

The profiles `minimum`, `standard` and `jumbo` send 18, 1472 and 8972-byte UDP payloads; `mixed`
mixes lengths. `--rate` sets the offered receive rate; transmit is capped by a 100G wire-time
model that counts preamble, FCS and gap. The model does not include the CMAC, the PCS or the
transceivers. Run enough packets to fill the queues: a short run can hide an overload.

### `bench/udp_throughput.py`: header path {#sim-udp-path}

`bench/udp_throughput.py` measures the UDP, IP and MAC header path alone, in cycles per packet,
with the Migen simulator. ARP is answered at once, and the PHY and the wire are left out. It
prints one JSON line per width and length; with `--output-dir`, it writes the Verilog of each
width instead, for an out-of-context timing build. The recorded results are in
[Performance and resources](#performance).

```sh
PYTHONPATH=. python3 bench/udp_throughput.py --data-width 128 256 512 --length 18 1472 8972 \
    --packets 4
```

## PHY refactoring checks {#sim-snapshot}

A PHY change that should not change the hardware can be checked structurally.
`test/phy_basex_snapshot.py` converts every BASE-X wrapper variant to Verilog and to a Yosys
netlist, and records a fingerprint of each one. It needs Yosys. The script inspects the `liteeth`
of the directory it runs from, wherever the script itself lives: run the same script, the one of
the new checkout, from the old checkout and then from the new one, with the same tool versions,
and compare the summaries. `<new>` stands for the path of the new checkout.

```sh
# In the old checkout.
python3 <new>/test/phy_basex_snapshot.py --output-dir <new>/build/basex-before
# In the new checkout.
python3 test/phy_basex_snapshot.py --output-dir build/basex-after
diff -u build/basex-before/summary.json build/basex-after/summary.json
```

The fingerprint covers the primitive parameters, port bit order, connections, constants and
register initial values, and ignores internal names. It is a regression aid, not a proof of
equivalence: review every difference.

## Adding a test {#sim-add-test}

1. Put a `test_<topic>.py` file in `test/` with a `unittest.TestCase` class; `pytest` finds it.
2. Build the module under test in a small `DUT` module, with the models of `test/model/` or the
   streamer and logger of `test/stream_helpers.py` around it, as `test/test_udp.py` does.
3. Check both directions and the error paths: bad CRC, runt and oversize frames, backpressure.
4. For behavior that depends on the generated Verilog, add a `_rtl.py` test with `run_rtl` and a
   `skipUnless` on the simulator.
5. Run the file, then the whole suite, before opening a pull request.

A problem found on a board belongs in a test once it is understood: reproduce it with the models,
fix it, and keep the test.

Where to go next: [Debugging](#debugging) for the board side; [Examples and targets](#examples)
for the designs these simulations mirror.
