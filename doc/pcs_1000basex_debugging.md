# 1000BASE-X / SGMII PCS — debugging guide

This document covers the LiteEth `liteeth.phy.pcs_1000basex.PCS` block:
how to verify it in simulation, and how to diagnose link-bring-up
failures on real hardware (in particular: links that work through an
SFP+/RJ45 adapter but fail when an FPGA SFP is plugged directly into a
switch SFP cage).

## TL;DR

  - Run the unit tests: `python3 -m unittest test.test_pcs_1000basex`.
  - On hardware, read `pcs_status`, `pcs_debug`, `pcs_restart_count`,
    write `pcs_debug_clear` to re-arm the sticky bits, then re-read.
    The `an_state` field tells you which auto-negotiation phase is
    failing.

## Background — why an adapter "just works"

An SFP+/RJ45 adapter is a real PHY chip. It runs a *fixed*, very
forgiving link with the FPGA on its high-speed side, and runs a
separate Clause 28 (twisted-pair) auto-negotiation with the switch on
the copper side. The FPGA only ever has to satisfy the adapter, which
will accept almost anything.

A direct SFP-to-switch link removes that buffer. The FPGA's Clause 37
PCS is now talking to a real switch's Clause 37 endpoint, and any
spec-corner-cutting in the local PCS surfaces immediately.

## Observability CSRs

Built when constructing the PHY with `with_csr=True` (the default for
all wrapper PHYs in `liteeth/phy/*_1000basex.py`):

| CSR                  | Field            | Meaning                                         |
| -------------------- | ---------------- | ----------------------------------------------- |
| `pcs_status`         | `link_up`        | Final link status                               |
|                      | `is_sgmii`       | SGMII identifier bit observed in partner config |
|                      | `config_reg`     | Last *consistent* partner config_reg            |
| `pcs_debug`          | `an_state`       | Current AN FSM state (see table below)          |
|                      | `seen_valid_ci`  | Sticky: any /C/ or /I/ ordered set decoded      |
|                      | `seen_config_abi`| Sticky: a partner ability config passed the     |
|                      |                  | 8-sample consistency check                      |
|                      | `seen_config_ack`| Sticky: a partner config with the ACK bit       |
|                      |                  | passed the consistency check                    |
|                      | `rx_invalid`     | Sticky: any 8b/10b decode error                 |
| `pcs_restart_count`  | -                | Saturating count of AN restarts                 |
| `pcs_debug_clear`    | -                | Write any value to clear sticky fields and the  |
|                      |                  | restart counter                                 |

`an_state` encoding:

| Value | State                 | Meaning                                         |
| ----- | --------------------- | ----------------------------------------------- |
| 0     | AUTONEG-BREAKLINK     | Sending empty config to force partner restart   |
| 1     | AUTONEG-WAIT-ABI      | Waiting for partner ability page                |
| 2     | AUTONEG-WAIT-ACK      | Waiting for partner config with ACK bit         |
| 3     | AUTONEG-SEND-MORE-ACK | Holding ACK config for `more_ack_time`          |
| 4     | AUTONEG-IDLE-DETECT   | Sending /I/, waiting `idle_detect_time` (1000BASE-X only) |
| 5     | RUNNING               | Link up, normal data                            |

## Diagnostic flow

Power-cycle / re-program the FPGA and read CSRs in this order. The
expected steady state is `an_state=5`, all four sticky bits set to 1,
`restart_count` stable.

1. `pcs_debug.an_state == 0` (BREAKLINK) and stuck:
   - Almost certainly impossible — this state always exits after
     `breaklink_time` (default 10 ms). Suspect a stuck reset on the AN
     FSM clock domain (`eth_tx`), or a constant restart from elsewhere.

2. `an_state == 1` (WAIT-ABI), `seen_valid_ci == 0`:
   - The PCS is not decoding any ordered set. Issue is below the PCS:
     the SerDes is not aligned, the polarity is inverted, or the SFP
     has no signal. Check `rx_invalid` (likely 1), check the SerDes
     alignment status, try flipping `rx_polarity` on the PHY wrapper.

3. `an_state == 1` (WAIT-ABI), `seen_valid_ci == 1`,
   `seen_config_abi == 0`:
   - We see /I/ or partial /C/ but never a full consistent ability
     config. Either the partner is not advertising (e.g. the SFP cage
     has AN disabled and is doing forced 1000BASE-X) or the partner
     keeps sending different configs. Try advertising a more
     compatible `tx_ability` (default `0x01A0` = FD + symmetric +
     asymmetric pause); some peers expect pause to be advertised.

4. `an_state == 2` (WAIT-ACK), `seen_config_abi == 1`,
   `seen_config_ack == 0`:
   - We saw the partner's ability page but never an ACK. Usually means
     the partner does not like our advertised abilities (pause
     mismatch, half-duplex advertised, remote-fault set). Adjust
     `tx_ability` and try again.

5. `an_state == 4` (IDLE-DETECT) bouncing:
   - Should not happen with current code (see commit `phy/pcs_1000basex:
     drop /C/-restart in IDLE_DETECT`). If it reappears, look for a
     spurious `rx_config_reg_abi` or `rx_config_reg_ack` pulse during
     IDLE_DETECT.

6. `an_state == 5` (RUNNING), `restart_count` climbing:
   - Link comes up but bounces. The checker is firing
     (`check_period=6 ms` of no /C/-or-/I/) - means the SerDes is
     occasionally dropping bytes. Look at `rx_invalid` (sticky).
     Could be a marginal optical signal or a clocking issue.

## Running the unit tests

```
cd /path/to/liteeth
python3 -m unittest test.test_pcs_1000basex -v
```

The tests are organised by scope:

  - `TestPCSStructure` — Python-only assertions on the constructor and
    the FSM topology. Fast (< 0.1s).
  - `TestPCSTX` — drives the TX FSM and inspects the encoder symbol
    stream for idle and config ordered sets. Fast.
  - `TestPCSRX` — drives an Encoder + Decoder pair and checks that the
    RX FSM raises `seen_valid_ci`, `seen_config_reg`, and captures
    `config_reg`.
  - `TestPCSLoopback` — instantiates one PCS with TBI tx/rx looped
    back, runs the full Clause 37 handshake, and asserts:
      * `link_up` reaches 1 within a fixed cycle budget,
      * `AUTONEG-IDLE-DETECT` is visited before `RUNNING`,
      * no AN restart occurs once `RUNNING` is reached.
    Slower (~45 s for the three loopback cases together) because the
    handshake is several thousand cycles even with the test timers
    scaled down ~1000x from the spec values.

## Hardware test plan

Recommended bring-up order against a misbehaving switch link:

1. **Build at the observability commit only**
   (`phy/pcs_1000basex: add observability CSRs for AN diagnostics`).
   No behaviour changes vs. before. Plug into the broken switch port,
   wait a few seconds, read `pcs_debug` / `pcs_restart_count`. Note the
   stuck state.

2. **Add the consistency-latching commit** (`...: latch partner
   config_reg after consistency check`). Re-test. If the failure was
   `is_sgmii` glitching mid-handshake, this alone may resolve it.

3. **Add the configurable `tx_ability` commit** and try the default
   `0x01A0` (FD + symmetric/asymmetric pause). Some switches refuse a
   peer that does not advertise pause.

4. **Add the IDLE_DETECT + restart-fix commits**. After this, the FSM
   matches Clause 37 figure 37-6 and the symmetric self-loopback test
   passes (it does not under any earlier commit on the branch).

If the link still does not come up after all four commits, the failure
is below the PCS - most likely SerDes alignment, polarity, or signal
quality. Capture `rx_invalid`, the SerDes alignment status, and try
flipping `rx_polarity` on the PHY wrapper.

## Files of interest

  - `liteeth/phy/pcs_1000basex.py` — the PCS itself.
  - `test/test_pcs_1000basex.py` — unit tests for the above.
  - `liteeth/phy/{a7,k7,v7,ku,usp_gth,usp_gty}_1000basex.py` and
    `liteeth/phy/titanium_lvds_1000basex.py` — board-specific wrappers.
    These instantiate the SerDes and connect `pcs.tbi_tx`, `pcs.tbi_rx`,
    `pcs.tbi_rx_ce`, and `pcs.align`.
