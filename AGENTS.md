# AGENTS.md: LiteEth

Ethernet core for LiteX: PHYs from 10BASE-T on IOs to 25GBASE-R on transceivers, MAC, hardware
ARP/ICMP/UDP/DHCP stack, Etherbone, UDP streamer, PTP, standalone generator.

Read the LiteX agent page first: https://github.com/enjoy-digital/litex/wiki/LiteX-for-AI-Agents (framework map, conventions, how to
verify a change, contribute back). Index of the whole wiki for agents: https://github.com/enjoy-digital/litex/wiki/llms.txt.

## Where things are

- `liteeth/phy/`: `parallel/` (MII/RMII/GMII/RGMII...), `serial/` (1000BASE-X/SGMII, 2.5G, 5/10/25GBASE-R per transceiver family), `simulation/`.
- `liteeth/mac/`, `liteeth/core/` (ARP/ICMP/IP/UDP/DHCP, PTP), `liteeth/frontend/` (Etherbone, stream/UDP).
- `liteeth/gen.py` (`liteeth_gen <config>.yml`, details in `doc/gen.md`): standalone core; `bench/`: hardware benches.

## Verify a change

```sh
python3 -m pytest -v                                               # what CI runs
litex_sim --with-ethernet                                          # MAC/stack in simulation (needs a TAP: sudo)
python3 -m litex_boards.targets.<board> --with-ethernet --build --no-compile
```

- PHY and transceiver changes need a board (link up, then traffic: Etherbone/ping/iperf-style).
- On contributed, hardware-tested code, send small fixes, one per commit, not rewrites.
- Generator details go to `doc/gen.md`, not the README.

## Rules

- Style: https://github.com/enjoy-digital/litex/blob/master/doc/coding_style.md (match the
  surrounding file; `module: Description of the change.` commit titles; one change per commit).
- Never edit generated files (`build/`, `csr.h`, `csr.csv`, the Verilog): change the Python.
- One topic per pull request, from `master`, with what was tested and on which hardware; say
  that an agent wrote it and who supervised. Do not rename options or reformat unrelated code.
- Hardware: never flash a board or write its SPI flash unless the user asked; `--load` (volatile)
  is the default for experiments.

## Debug on hardware

Method: https://github.com/enjoy-digital/litex/wiki/Debug-A-SoC-On-Hardware (check the link and `--ident` first, registers before
traces, narrow LiteScope captures). `litex_mcp` gives the same loop as MCP tools (CSRs by name,
LiteScope captures, builds with parsed timing, simulation): https://github.com/enjoy-digital/litex/wiki/LiteX-for-AI-Agents#drive-a-soc-through-mcp.
