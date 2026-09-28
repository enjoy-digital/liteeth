# Porting Transceiver Ethernet PHYs

LiteEth keeps Ethernet protocol logic separate from the transceiver mode and
board wiring. A PHY port should reuse the existing PCS, expose the same MAC
interface, and add only the PMA behavior the new device needs. The broader
LiteX architecture rules are documented in
[LiteX's architectural style guide](https://github.com/enjoy-digital/litex/blob/master/doc/architectural_style.md).

## Ownership and Interfaces

| Component | Location | Responsibility |
| --- | --- | --- |
| 1000/2500BASE-X PCS | `liteeth/phy/serial/basex/pcs.py` | 8b/10b Ethernet coding, autonegotiation, and the byte stream |
| BASE-X PMAs | `liteeth/phy/serial/basex/pma/` | Raw symbols, vendor primitives, clocking, gearboxes, and device initialization |
| 5/10/25GBASE-R PCS | `liteeth/phy/serial/baser/pcs/` | 64b/66b coding, scrambling, block sync, BER, and XGMII |
| BASE-R PMAs | `liteeth/phy/serial/baser/pma/` | Vendor primitive in 64b/66b mode, gearbox cadence, clock/reset, and bitslip |
| PHY wrappers | `liteeth/phy/serial/{basex,baser}/wrappers/` | MAC stream, PCS/PMA wiring, public controls, and CSRs |
| Parallel PHYs | `liteeth/phy/parallel/` | MII, RMII, GMII, XGMII, and vendor RGMII adapters |
| Simulation PHY | `liteeth/phy/simulation/model.py` | Stream-based model for software and gateware simulation |
| Shared transceiver helpers | `liteiclink/serdes/` | PLLs, DRP, and applicable initialization sequences |
| Board targets and platforms | LiteX targets/platforms | Pins, reference clock source, transceiver channel, and timing constraints |
| Standalone generator | `liteeth/gen.py` | Core configuration, PHY selection, and rate-compatible reference clock defaults |

The BASE-R PCS presents a 64-bit block and two sync-header bits in each
direction. Its PMA contract includes `tx_data`, `tx_header`, `rx_data`,
`rx_header`, `rx_slip`, TX/RX clock domains, loopback, and reset. The 7-series
PMA also provides `tx_ce` and `rx_ce`: its gearbox pauses, so the PCS and
XGMII adapters must advance only when a block moves. UltraScale+ transfers
one block per user clock and does not need those enables. The PHY wrapper
handles that difference; the PCS contains no device-family conditionals.

The 1000/2500BASE-X PCS uses 8b/10b symbols and a byte stream. It cannot
share the BASE-R PCS or its 64b/66b PMA interface. Rate variants within one
transceiver family should instead share a wrapper and state their allowed
reference clocks explicitly. For example, `K7_1000BASEX` uses a 200 MHz
reference, while `K7_2500BASEX` needs 125 MHz for its 3.125 Gb/s channel
PLL configuration.

Virtex-7 GTH BASE-X supports 200 MHz and 156.25 MHz references at 1G, with
200 MHz as the default. `V7_2500BASEX` uses a 156.25 MHz reference by default
and rejects 200 MHz: the channel PLL cannot generate 3.125 Gb/s from that
reference. The calibration divider follows the reference frequency, and
the CDR configuration follows the PLL's output divider.

KU and UltraScale+ 2.5G BASE-X wrappers also default to 156.25 MHz; a 200 MHz
reference cannot generate their 3.125 Gb/s line rate. The standalone generator
uses these defaults when `refclk_freq` is omitted. Explicit reference-clock
settings remain authoritative.

### BASE-X Device Boundary

The Xilinx GTP/GTX/GTH/GTY BASE-X wrappers construct a PCS and a PMA. Their
PMA interface is deliberately a symbol interface, without MAC backpressure:

| PMA signal | Direction | Domain / meaning |
| --- | --- | --- |
| `tx_data[9:0]` | Input | One 8b/10b symbol per `eth_tx` clock |
| `rx_data[9:0]` | Output | One 8b/10b symbol per `eth_rx` clock |
| `rx_valid` | Output | Qualifies `rx_data`; continuously high on these transceivers |
| `align` | Input | PCS alignment request from `eth_tx` |
| `restart` | Input | PCS receiver-restart pulse from `eth_tx` |
| `reset` | Input | External PHY reset request; device reset handling remains in the PMA |

The PMA owns `eth_tx`, `eth_rx`, and their half-rate domains, along with the
10/20-bit `PCSGearbox`. A7/K7/V7 synchronize restart into `sys`; KU/USP retain
their existing direct transceiver-reset paths. Do not change those sequences
as part of a file move. The wrapper exposes the same clock, PLL, init, and
gearbox objects used by existing targets, without registering them twice.
The PMAs contain no Ethernet PCS, autonegotiation policy, or PHY CSRs.

A7 additionally preserves `gtp_params` and the wrapper's `do_finalize` hook:
the PMA builds the parameter dictionary, while the wrapper instantiates the
channel after downstream finalization overrides. A standalone A7 PMA
instantiates its channel itself; only the compatibility wrapper disables
that with `with_channel=False`.

GW5 and LVDS use their existing native interfaces: GW5 qualifies received
symbols with `rx_valid`, and LVDS clock recovery and alignment differ by
device. Their helper classes live in `pma/`, while existing PHY recovery,
MAC buffering, constraints, and CSR wiring remain explicit in the wrappers.
Sharing a directory or ownership model does not require identical interfaces.

## Reuse with LiteICLink

The BASE-R GTY/GTH/GTX PMAs already reuse LiteICLink PLL, DRP, and reset/init
helpers. The 7-series GTP PMA currently reuses the older GTP initialization
logic in `liteeth/phy/serial/gtp_7series.py`. LiteICLink's generic
GTP/GTX/GTH/GTY SerDes classes contain 8b/10b encoding and are limited to
their 20/40-bit data modes. Their primitive instances cannot replace a BASE-R PMA without
adding the 64b/66b gearbox and matching its clock and reset behavior. The
device-specific primitive parameter tables therefore remain in the PMAs.

The BASE-R PHYs share their PRBS error counter and CSR layout through
`serial/baser/wrappers/diagnostics.py` (`LiteEthBASERPHY`). The PMA clocking, restart
path, XGMII pipeline, and gearbox sequencing stay in the device wrappers
because their timing differs.
Before moving another helper to LiteICLink, compare its reset sequence and
primitive parameters at every supported rate, then check on hardware. In
particular, replacing the GTP initialization path needs reset and DRP
validation on an Artix-7 board.

## Porting and Validation

1. Pick the existing PCS for the Ethernet mode. Define the PMA data width,
   clock domains, block cadence, bitslip, and reset request before writing a
   vendor primitive instance.
2. Reuse LiteICLink PLL, DRP, and initialization helpers when their timing
   contract matches. Keep the board's reference-clock and pad selection in
   its target.
3. Add the new rate or device as a small wrapper or PMA variant. Keep class
   names, stream width, CSR layout, and clock-domain names stable for existing
   targets. Record the source of rate-dependent primitive parameters.
4. Run `pytest -q test/test_phy_portability.py test/test_pcs_baser.py
   test/test_pcs_1000basex.py`. The first test elaborates supported wrappers,
   checks their primitive and public interface, and exercises the shared
   PRBS counter. The PCS tests cover protocol behavior without a vendor tool.
5. Build a representative target with the FPGA toolchain and check clock
   constraints, timing, link-up, traffic, and reset recovery on hardware.
   Elaborating a primitive verifies its structure, not analog behavior or
   timing closure.

For a structural BASE-X comparison, run `test/phy_basex_snapshot.py` from
each checkout under the same Python/LiteX/LiteICLink and Yosys versions:

```sh
python3 /path/to/new-checkout/test/phy_basex_snapshot.py --output-dir /tmp/basex-before
# Repeat from the new checkout:
python3 test/phy_basex_snapshot.py --output-dir /tmp/basex-after
diff -u /tmp/basex-before/summary.json /tmp/basex-after/summary.json
```

The utility retains Verilog, ROM initialization, and Yosys JSON, and compares
structural fingerprints including primitive parameters, port bit order,
connectivity, and register initialization. It ignores internal names and
normalizes commutative reduction inputs. This is a regression aid, not a
formal equivalence proof. Review every difference; compare an adapter
refactor against separately fixed code if a pre-existing bug changes hardware.
Run `test/test_basex_boundary.py` for PCS/raw-symbol loopback, receive-valid
gating, restart requests, packets, and recovery after reset, in addition to
the existing PCS, LVDS, GW5, generator, and full pytest suites.

## Reading BASE-R Diagnostics

The per-block error field reports a coherently sampled receive block, not every
block. The accumulated PRBS counter saturates at its configured width and only
advances on valid receive blocks when the PMA supplies an enable.

To read the complete counter, including multiple CSR words:

1. Clear `control.prbs_pause` and wait for `status.prbs_paused` to be zero.
2. Enable the PRBS checker and leave it enabled throughout the measurement.
3. Set `control.prbs_pause` and poll `status.prbs_paused` until it is one.
4. Read `rx_prbs_errors`; the acknowledgment travels with the frozen counter value.
5. Clear `control.prbs_pause` and wait for `status.prbs_paused` to become zero
   before requesting another snapshot.

Disabling the checker or resetting its receive domain clears the counter,
including while paused. Existing register addresses and field offsets remain
unchanged; `status.prbs_paused` occupies previously unused bit 12 and is zero
when PRBS support is disabled. Software polling must use a timeout if the
receive clock can stop.
Reading `status` retains its existing read-to-clear behavior for the sticky
link-loss and high-BER flags, including while polling the pause acknowledgment.
