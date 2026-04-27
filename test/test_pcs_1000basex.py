#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Tests for liteeth.phy.pcs_1000basex.

Covers:
  - tx_ability constructor validation and bit mapping.
  - PCSTX ordered-set generation (idle, config, frame).
  - PCSRX ordered-set decoding (idle, config, frame).
  - Full PCS auto-negotiation in self-loopback (1000BASE-X path),
    including the IDLE_DETECT state and observability CSRs.
"""

import unittest

from migen import *
from litex.gen import *
from litex.gen.sim import run_simulation, passive
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.phy.pcs_1000basex import PCS, PCSTX, PCSRX

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

# 8b/10b ordered-set bytes used by 1000BASE-X / SGMII.
K28_5 = (K(28, 5), 1)  # comma
C1_D  = (D(21, 5), 0)  # /C1/ second byte
C2_D  = (D( 2, 2), 0)  # /C2/ second byte
I1_D  = (D( 5, 6), 0)  # /I1/ second byte (preserves disparity)
I2_D  = (D(16, 2), 0)  # /I2/ second byte (flips disparity)
S_K   = (K(27, 7), 1)  # /S/ start-of-packet
T_K   = (K(29, 7), 1)  # /T/ end-of-packet
R_K   = (K(23, 7), 1)  # /R/ carrier extend


def _short_pcs(**overrides):
    """PCS with short timers for fast simulation.

    The defaults are scaled down by ~1000x from the spec so an AN handshake
    completes in a few thousand cycles instead of several million.
    """
    kwargs = dict(
        lsb_first        = False,
        check_period     = 10e-6,  # ~1250 cycles at 125 MHz
        breaklink_time   =  2e-6,  #  ~250 cycles
        more_ack_time    =  2e-6,
        sgmii_ack_time   =  2e-6,
        idle_detect_time =  2e-6,
    )
    kwargs.update(overrides)
    return PCS(**kwargs)


# ----------------------------------------------------------------------------
# Structural tests (no simulation)
# ----------------------------------------------------------------------------

class TestPCSStructure(unittest.TestCase):
    def test_default_tx_ability(self):
        # Default advertises FD + symmetric/asymmetric pause.
        pcs = _short_pcs()
        self.assertEqual(pcs.tx_ability, 0x01a0)

    def test_tx_ability_rejects_sgmii_id_bit(self):
        # Bit 0 is reserved for the SGMII-in-use identifier driven by AN FSM.
        with self.assertRaises(AssertionError):
            _short_pcs(tx_ability=0x0001)

    def test_tx_ability_rejects_ack_bit(self):
        # Bit 14 is the ACK bit driven by AN FSM.
        with self.assertRaises(AssertionError):
            _short_pcs(tx_ability=0x4000)

    def test_tx_ability_accepts_user_pattern(self):
        # FD only (= legacy default before this change).
        pcs = _short_pcs(tx_ability=0x0020)
        self.assertEqual(pcs.tx_ability, 0x0020)

    def test_an_fsm_includes_idle_detect(self):
        # IDLE_DETECT was missing in earlier revisions of the FSM.
        pcs = _short_pcs()
        self.assertIn("AUTONEG-IDLE-DETECT", pcs.an_fsm.actions)

    def test_an_fsm_state_set(self):
        pcs = _short_pcs()
        self.assertEqual(set(pcs.an_fsm.actions.keys()), {
            "AUTONEG-BREAKLINK",
            "AUTONEG-WAIT-ABI",
            "AUTONEG-WAIT-ACK",
            "AUTONEG-SEND-MORE-ACK",
            "AUTONEG-IDLE-DETECT",
            "RUNNING",
        })


# ----------------------------------------------------------------------------
# PCSTX behavior
# ----------------------------------------------------------------------------

class _PCSTXDUT(LiteXModule):
    """Wraps PCSTX so it runs in the default sys clock domain (no rename)."""
    def __init__(self):
        self.submodules.tx = PCSTX(lsb_first=False)


def _decode_tx(dut, n_cycles):
    """Sample (d, k) pairs out of the encoder for `n_cycles` cycles.

    Each cycle the encoder emits one symbol; we read the symbolic
    `encoder.d[0]` / `encoder.k[0]` (the pre-10b/8b inputs) as that is
    what the FSM drives directly.
    """
    out = []
    for _ in range(n_cycles):
        d = (yield dut.tx.encoder.d[0])
        k = (yield dut.tx.encoder.k[0])
        out.append((d, k))
        yield
    return out


class TestPCSTX(unittest.TestCase):
    def test_idle_emits_K28_5_then_D(self):
        """In idle, TX must emit alternating K28.5 / D5.6 or D16.2."""
        dut = _PCSTXDUT()

        result = []
        def gen():
            yield dut.tx.sgmii_speed.eq(0b10)  # 1Gbps
            # Let it settle out of START into IDLE then back, capture symbols.
            for _ in range(2):
                yield
            for _ in range(20):
                d = (yield dut.tx.encoder.d[0])
                k = (yield dut.tx.encoder.k[0])
                result.append((d, k))
                yield

        run_simulation(dut, gen())

        # Among captured symbols there must be at least one K28.5 (comma) and
        # one of the two idle data symbols.
        self.assertIn(K28_5, result)
        idle_d_seen = any(s in (I1_D, I2_D) for s in result)
        self.assertTrue(idle_d_seen, f"no /I1/ or /I2/ in {result}")

    def test_config_emits_ordered_set(self):
        """With config_valid=1, TX emits K28.5 / Cx_D / cfg_lsb / cfg_msb."""
        dut = _PCSTXDUT()

        result = []
        def gen():
            yield dut.tx.sgmii_speed.eq(0b10)
            yield dut.tx.config_reg.eq(0xBEEF)
            yield dut.tx.config_valid.eq(1)
            for _ in range(2):
                yield
            for _ in range(40):
                d = (yield dut.tx.encoder.d[0])
                k = (yield dut.tx.encoder.k[0])
                result.append((d, k))
                yield

        run_simulation(dut, gen())

        # Must see K28.5, at least one of /C1/-D or /C2/-D, and the config
        # bytes 0xEF (LSB) and 0xBE (MSB) appear as data symbols.
        self.assertIn(K28_5, result)
        c_seen = any(s in (C1_D, C2_D) for s in result)
        self.assertTrue(c_seen, f"no /C1/ or /C2/ in {result}")
        data_bytes = [d for (d, k) in result if k == 0]
        self.assertIn(0xEF, data_bytes)
        self.assertIn(0xBE, data_bytes)


# ----------------------------------------------------------------------------
# PCSRX behavior (driven through a real Encoder so disparity is correct)
# ----------------------------------------------------------------------------

class _PCSRXDUT(LiteXModule):
    """Encoder + PCSRX wired together.

    The PCSRX FSM consumes the registered outputs of an internal 8b/10b
    Decoder, so the testbench cannot override decoder.d / decoder.k
    directly (the Decoder's sync logic would overwrite them on the next
    clock edge). Instead, an Encoder is driven by the testbench and its
    10-bit output is fed into decoder.input.
    """
    def __init__(self):
        self.submodules.enc = Encoder(lsb_first=False)
        self.submodules.rx  = PCSRX(lsb_first=False)
        self.comb += [
            self.rx.decoder.input.eq(self.enc.output[0]),
            self.rx.decoder.ce.eq(1),
        ]


def _drive_encoder(dut, symbols):
    """Drive a sequence of (d, k) symbols through the Encoder."""
    for (d, k) in symbols:
        yield dut.enc.d[0].eq(d)
        yield dut.enc.k[0].eq(k)
        yield


class TestPCSRX(unittest.TestCase):
    # Encoder + Decoder pipeline introduces ~2 cycles of latency, so the FSM
    # observes each driven symbol a few cycles after we set it. We sample
    # signals across a window large enough to absorb that.

    def test_idle_pulses_seen_valid_ci(self):
        dut = _PCSRXDUT()

        seen = []
        def gen():
            stream = [K28_5, I1_D, K28_5, I2_D, K28_5, I1_D, K28_5, I2_D]
            yield from _drive_encoder(dut, stream)
            for _ in range(8):
                seen.append((yield dut.rx.seen_valid_ci))
                yield

        run_simulation(dut, gen())
        self.assertIn(1, seen,
            f"seen_valid_ci never pulsed across {len(seen)} samples")

    def test_config_pulses_seen_config_reg(self):
        dut = _PCSRXDUT()

        cfg_captured = []
        seen_cfg     = []
        def gen():
            # Two full /C/ ordered sets carrying 0xABCD.
            stream = [
                K28_5, C1_D, (0xCD, 0), (0xAB, 0),
                K28_5, C2_D, (0xCD, 0), (0xAB, 0),
            ]
            yield from _drive_encoder(dut, stream)
            for _ in range(8):
                pulse = (yield dut.rx.seen_config_reg)
                seen_cfg.append(pulse)
                if pulse:
                    cfg_captured.append((yield dut.rx.config_reg))
                yield

        run_simulation(dut, gen())
        self.assertIn(1, seen_cfg, "seen_config_reg never pulsed for /C/")
        self.assertIn(0xABCD, cfg_captured,
            f"config_reg never captured 0xABCD; got {cfg_captured}")


# ----------------------------------------------------------------------------
# PCS auto-negotiation in self-loopback (1000BASE-X path)
# ----------------------------------------------------------------------------

class _PCSLoopbackDUT(LiteXModule):
    """A single PCS instance with TBI looped back tx -> rx.

    In a symmetric AN like 1000BASE-X / SGMII, looping the TX symbols back
    into the RX is enough to drive the AN FSM through the full handshake:
    every config the local end sends is the config the local end receives.

    `state_probes` exposes one Signal per AN FSM state so the testbench can
    sample which state is active each cycle. `fsm.ongoing(...)` cannot be
    called after the FSM is finalized, so the probes are wired up here.
    """
    def __init__(self, **kwargs):
        # The PCS internally renames sub-FSMs into eth_tx / eth_rx — the
        # parent module must declare those clock domains.
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.submodules.pcs = PCS(**kwargs)
        # TBI loopback. tbi_tx is in eth_tx, tbi_rx is in eth_rx; in sim we
        # tie both clocks to the same period so this comb wire is stable.
        self.comb += [
            self.pcs.tbi_rx.eq(self.pcs.tbi_tx),
            self.pcs.tbi_rx_ce.eq(1),
        ]
        # Pre-register one probe per AN FSM state.
        self.state_probes = {
            name: self.pcs.an_fsm.ongoing(name)
            for name in self.pcs.an_fsm.actions.keys()
        }


class TestPCSLoopback(unittest.TestCase):
    # Cycles to allow for the full AN handshake with the short test timers.
    AN_BUDGET_CYCLES = 8000

    def _run_loopback(self, **pcs_kwargs):
        kwargs = dict(
            lsb_first        = False,
            check_period     = 10e-6,
            breaklink_time   =  2e-6,
            more_ack_time    =  2e-6,
            sgmii_ack_time   =  2e-6,
            idle_detect_time =  2e-6,
        )
        kwargs.update(pcs_kwargs)
        dut = _PCSLoopbackDUT(**kwargs)

        # State trace, sampled in eth_tx so we observe the AN FSM directly.
        states_visited = set()
        link_up_history = []

        def trace_gen():
            for _ in range(self.AN_BUDGET_CYCLES):
                for name, probe in dut.state_probes.items():
                    if (yield probe):
                        states_visited.add(name)
                link_up_history.append((yield dut.pcs.link_up))
                yield

        run_simulation(
            dut,
            generators = {"eth_tx": trace_gen()},
            clocks     = {"sys": 8, "eth_tx": 8, "eth_rx": 8},
        )

        return states_visited, link_up_history, dut

    def test_an_reaches_running_and_link_up(self):
        """1000BASE-X self-loopback brings link_up high within the budget."""
        states, link_up, _ = self._run_loopback()

        # Diagnostic: include the visited-state set in the assertion message.
        self.assertEqual(link_up[-1], 1,
            f"link_up never asserted; states visited: {sorted(states)}")

    def test_an_visits_idle_detect_before_running(self):
        """The 1000BASE-X path must transit IDLE_DETECT before LINK_OK."""
        states, _, _ = self._run_loopback()
        self.assertIn("AUTONEG-IDLE-DETECT", states,
            f"IDLE-DETECT was skipped; states visited: {sorted(states)}")
        self.assertIn("RUNNING", states)

    def test_an_does_not_restart_in_steady_state(self):
        """Once RUNNING, no restart should happen for the rest of the budget."""
        dut = _PCSLoopbackDUT(
            lsb_first        = False,
            check_period     = 10e-6,
            breaklink_time   =  2e-6,
            more_ack_time    =  2e-6,
            sgmii_ack_time   =  2e-6,
            idle_detect_time =  2e-6,
        )

        running_probe = dut.state_probes["RUNNING"]

        running_seen  = []
        restart_after = []
        def trace_gen():
            in_running = False
            for _ in range(self.AN_BUDGET_CYCLES):
                if (yield running_probe):
                    in_running = True
                if in_running:
                    running_seen.append(1)
                    restart_after.append((yield dut.pcs.restart))
                yield

        run_simulation(
            dut,
            generators = {"eth_tx": trace_gen()},
            clocks     = {"sys": 8, "eth_tx": 8, "eth_rx": 8},
        )

        self.assertGreater(len(running_seen), 0, "AN never reached RUNNING")
        self.assertNotIn(1, restart_after,
            "PCS restarted AN after reaching RUNNING in self-loopback")


# ----------------------------------------------------------------------------
# Two-PCS back-to-back framework (Layer 1)
# ----------------------------------------------------------------------------
#
# The self-loopback tests above verify behavior in the perfectly-symmetric
# case (both ends share one FSM). Real hardware - and especially direct
# SFP-to-switch links - is asymmetric: each end advertises different
# abilities, runs different timer values, and starts at unpredictable
# times relative to the other. A two-instance back-to-back DUT lets us
# exercise that.

class _PCSBackToBackDUT(LiteXModule):
    """Two PCS instances with cross-connected TBI.

    Each side may be constructed with different `tx_ability` and timer
    values to exercise capability- and timing-asymmetric handshakes.
    Both sides share the same eth_tx / eth_rx clock domains in sim - in
    real hardware each side would have its own recovered RX clock, but
    that asymmetry is not what we are trying to catch here.
    """
    def __init__(self, a_kwargs=None, b_kwargs=None):
        a_kwargs = a_kwargs or {}
        b_kwargs = b_kwargs or {}

        self.clock_domains.cd_eth_tx = ClockDomain()
        self.clock_domains.cd_eth_rx = ClockDomain()

        self.submodules.pcs_a = PCS(**_b2b_pcs_kwargs(a_kwargs))
        self.submodules.pcs_b = PCS(**_b2b_pcs_kwargs(b_kwargs))

        # Cross-connect TBI: A's TX -> B's RX, B's TX -> A's RX.
        self.comb += [
            self.pcs_a.tbi_rx.eq(self.pcs_b.tbi_tx),
            self.pcs_a.tbi_rx_ce.eq(1),
            self.pcs_b.tbi_rx.eq(self.pcs_a.tbi_tx),
            self.pcs_b.tbi_rx_ce.eq(1),
        ]

        # State probes for both AN FSMs.
        self.probes_a = {
            n: self.pcs_a.an_fsm.ongoing(n)
            for n in self.pcs_a.an_fsm.actions.keys()
        }
        self.probes_b = {
            n: self.pcs_b.an_fsm.ongoing(n)
            for n in self.pcs_b.an_fsm.actions.keys()
        }


def _b2b_pcs_kwargs(overrides):
    """Default PCS kwargs for back-to-back sim (short timers)."""
    kwargs = dict(
        lsb_first        = False,
        check_period     = 10e-6,
        breaklink_time   =  2e-6,
        more_ack_time    =  2e-6,
        sgmii_ack_time   =  2e-6,
        idle_detect_time =  2e-6,
    )
    kwargs.update(overrides)
    return kwargs


def _b2b_run(dut, n_cycles):
    """Run the back-to-back DUT for n_cycles, return per-cycle trace.

    Trace is a list of dicts with keys:
      cycle, state_a, state_b, link_up_a, link_up_b, restart_a, restart_b
    """
    trace = []
    def gen():
        for c in range(n_cycles):
            entry = {"cycle": c}
            for name, p in dut.probes_a.items():
                if (yield p):
                    entry["state_a"] = name
            for name, p in dut.probes_b.items():
                if (yield p):
                    entry["state_b"] = name
            entry["link_up_a"] = (yield dut.pcs_a.link_up)
            entry["link_up_b"] = (yield dut.pcs_b.link_up)
            entry["restart_a"] = (yield dut.pcs_a.restart)
            entry["restart_b"] = (yield dut.pcs_b.restart)
            trace.append(entry)
            yield

    run_simulation(
        dut,
        generators = {"eth_tx": gen()},
        clocks     = {"sys": 8, "eth_tx": 8, "eth_rx": 8},
    )
    return trace


class TestPCSBackToBack(unittest.TestCase):
    BUDGET_CYCLES = 8000

    def _both_link_up(self, trace, msg=""):
        last = trace[-1]
        states_a = sorted({e.get("state_a") for e in trace if e.get("state_a")})
        states_b = sorted({e.get("state_b") for e in trace if e.get("state_b")})
        self.assertEqual(last["link_up_a"], 1,
            f"side A never linked up{msg}; A states={states_a} B states={states_b}")
        self.assertEqual(last["link_up_b"], 1,
            f"side B never linked up{msg}; A states={states_a} B states={states_b}")

    def test_b2b_symmetric(self):
        """Two PCS with identical defaults link up."""
        dut = _PCSBackToBackDUT()
        trace = _b2b_run(dut, self.BUDGET_CYCLES)
        self._both_link_up(trace, " (symmetric)")

    def test_b2b_asymmetric_abilities(self):
        """A advertises FD-only; B advertises FD + pause. Should still link up."""
        dut = _PCSBackToBackDUT(
            a_kwargs = {"tx_ability": 0x0020},   # FD only
            b_kwargs = {"tx_ability": 0x01a0},   # FD + sym + asym pause
        )
        trace = _b2b_run(dut, self.BUDGET_CYCLES)
        self._both_link_up(trace, " (asymmetric abilities)")

    def test_b2b_asymmetric_timers(self):
        """A and B have different more_ack_time / idle_detect_time."""
        dut = _PCSBackToBackDUT(
            a_kwargs = {"more_ack_time": 1e-6, "idle_detect_time": 1e-6},
            b_kwargs = {"more_ack_time": 4e-6, "idle_detect_time": 4e-6},
        )
        trace = _b2b_run(dut, self.BUDGET_CYCLES)
        self._both_link_up(trace, " (asymmetric timers)")

    def test_b2b_no_restart_in_steady_state(self):
        """Once both sides are RUNNING, neither should restart for the rest of the run."""
        dut = _PCSBackToBackDUT()
        trace = _b2b_run(dut, self.BUDGET_CYCLES)
        # Find first cycle where both are RUNNING.
        for i, e in enumerate(trace):
            if e["link_up_a"] and e["link_up_b"]:
                first_running = i
                break
        else:
            self.fail("never reached steady state")
        for e in trace[first_running:]:
            self.assertEqual(e["restart_a"], 0,
                f"A restarted at cycle {e['cycle']} after reaching RUNNING")
            self.assertEqual(e["restart_b"], 0,
                f"B restarted at cycle {e['cycle']} after reaching RUNNING")


# ----------------------------------------------------------------------------
# Programmable HDL peer (Layer 2)
# ----------------------------------------------------------------------------
#
# A PCSPeer is an Encoder + Decoder pair driven by the testbench. The
# testbench writes (d, k) to the encoder each cycle to control exactly
# what we send to the device-under-test, and reads decoded (d, k,
# invalid) to observe what the DUT sent back. This lets us script
# specific switch quirks (AN-disabled, remote fault, NP=1, etc.) that a
# back-to-back peer cannot easily produce.

class _PCSPeer(LiteXModule):
    def __init__(self):
        # Outward-facing TBI.
        self.tbi_tx = Signal(10)  # to DUT.tbi_rx
        self.tbi_rx = Signal(10)  # from DUT.tbi_tx

        # Encoder for what we transmit.
        self.submodules.enc = Encoder(lsb_first=False)
        # Optional override: if raw_valid is high, raw_value bypasses
        # the encoder. Used for injecting invalid 8b/10b codes.
        self.raw_valid = Signal()
        self.raw_value = Signal(10)
        self.comb += If(self.raw_valid,
            self.tbi_tx.eq(self.raw_value),
        ).Else(
            self.tbi_tx.eq(self.enc.output[0]),
        )

        # Decoder for what we receive.
        self.submodules.dec = Decoder(lsb_first=False)
        self.comb += [
            self.dec.input.eq(self.tbi_rx),
            self.dec.ce.eq(1),
        ]


class _PCSWithPeerDUT(LiteXModule):
    """A single PCS connected to a programmable peer."""
    def __init__(self, **pcs_kwargs):
        self.clock_domains.cd_eth_tx = ClockDomain()
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.submodules.pcs  = PCS(**_b2b_pcs_kwargs(pcs_kwargs))
        self.submodules.peer = _PCSPeer()
        self.comb += [
            self.pcs.tbi_rx.eq(self.peer.tbi_tx),
            self.pcs.tbi_rx_ce.eq(1),
            self.peer.tbi_rx.eq(self.pcs.tbi_tx),
        ]
        self.probes = {
            n: self.pcs.an_fsm.ongoing(n)
            for n in self.pcs.an_fsm.actions.keys()
        }


# Peer testbench helpers - each is a generator that yields one symbol
# per clock cycle through the peer's encoder.

def _peer_emit_symbol(peer, d, k, raw=False):
    """Drive one (d, k) symbol through the peer encoder for one cycle."""
    if raw:
        yield peer.raw_valid.eq(1)
        yield peer.raw_value.eq(d)  # `d` carries a raw 10-bit code here.
    else:
        yield peer.raw_valid.eq(0)
        yield peer.enc.d[0].eq(d)
        yield peer.enc.k[0].eq(k)
    yield


def _peer_emit_idle(peer, n_ordered_sets):
    """Emit `n_ordered_sets` /I/ ordered sets (K28.5 + /I1/ or /I2/)."""
    for i in range(n_ordered_sets):
        yield from _peer_emit_symbol(peer, K(28, 5), 1)
        # Alternate /I1/ and /I2/.
        if i % 2 == 0:
            yield from _peer_emit_symbol(peer, D(5, 6), 0)
        else:
            yield from _peer_emit_symbol(peer, D(16, 2), 0)


def _peer_emit_config(peer, config_reg, n_ordered_sets):
    """Emit `n_ordered_sets` /C/ ordered sets carrying `config_reg`."""
    for i in range(n_ordered_sets):
        yield from _peer_emit_symbol(peer, K(28, 5), 1)
        # Alternate /C1/ and /C2/.
        if i % 2 == 0:
            yield from _peer_emit_symbol(peer, D(21, 5), 0)
        else:
            yield from _peer_emit_symbol(peer, D( 2, 2), 0)
        yield from _peer_emit_symbol(peer, config_reg & 0xFF, 0)
        yield from _peer_emit_symbol(peer, (config_reg >> 8) & 0xFF, 0)


def _read_pcs_state(dut):
    """Return the name of the AN FSM state currently active in `dut.pcs`."""
    for name, p in dut.probes.items():
        if (yield p):
            return name
    return None


def _peer_passive(peer, body):
    """Wrap a peer-test body so it keeps emitting /I/ forever after the
    scripted sequence finishes, and so the simulator does not block on it.

    This matters because a real link is never silent - if we stop driving
    the peer's encoder, the PCS sees a frozen 10-bit value, the decoder
    chokes on it, the checker fires, and AN restarts artificially.
    """
    @passive
    def gen():
        yield from body
        # Hold idle indefinitely; observe-side ends the simulation.
        while True:
            yield from _peer_emit_idle(peer, 64)
    return gen()


class TestPCSWithPeer(unittest.TestCase):
    """Switch-quirk scenarios driven by a programmable peer."""

    BUDGET_CYCLES = 12000

    def _run(self, dut, peer_body, observe_gen=None):
        """Run dut with the peer driving and an optional observer.

        `peer_body` is a generator that emits the scripted symbol
        sequence; it is automatically wrapped in `_peer_passive` so it
        keeps the link populated with /I/ after the scripted part is
        done. The observer (non-passive) bounds the simulation.
        """
        peer_gen = _peer_passive(dut.peer, peer_body)
        gens = {"eth_tx": [peer_gen]}
        if observe_gen is not None:
            gens["eth_tx"].append(observe_gen)
        run_simulation(
            dut, gens,
            clocks = {"sys": 8, "eth_tx": 8, "eth_rx": 8},
        )

    # ----- Scenario 1: peer with AN disabled (only emits /I/) -----

    def test_peer_an_disabled_no_link(self):
        """A peer that only emits /I/ (no /C/) should never bring link_up.

        And we should not restart-storm: stay in WAIT-ABI without
        cycling back through BREAKLINK on the checker.
        """
        dut = _PCSWithPeerDUT()

        link_seen      = []
        states_visited = set()
        restart_count  = [0]
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                link_seen.append((yield dut.pcs.link_up))
                s = (yield from _read_pcs_state(dut))
                if s:
                    states_visited.add(s)
                if (yield dut.pcs.restart):
                    restart_count[0] += 1
                yield

        def peer():
            yield from _peer_emit_idle(dut.peer, self.BUDGET_CYCLES // 2)

        self._run(dut, peer(), observe())

        self.assertEqual(max(link_seen), 0,
            "link_up asserted against an AN-disabled peer")
        # We can land in WAIT-ABI; we must not loop indefinitely back to
        # BREAKLINK because the checker should be cleared by /I/.
        self.assertLess(restart_count[0], 5,
            f"too many restarts ({restart_count[0]}) against AN-disabled peer; "
            f"states visited: {sorted(states_visited)}")

    # ----- Scenario 2: peer stuck in COMPLETE_ACKNOWLEDGE longer than us -----

    def test_peer_slow_complete_ack(self):
        """A peer that holds /C/+ACK 5x longer than our timer must not
        cause us to restart in IDLE_DETECT."""
        dut = _PCSWithPeerDUT(more_ack_time=1e-6, idle_detect_time=1e-6)

        # Walk the AN dance manually: send ABI, then ACK, then keep
        # sending ACK for a long time, then fall to /I/.
        def peer():
            # Plenty of /C/ with ability config (no ack) so we exit WAIT-ABI.
            yield from _peer_emit_config(dut.peer, 0x01a0, 30)
            # /C/ with ACK so we exit WAIT-ACK.
            yield from _peer_emit_config(dut.peer, 0x41a0, 30)
            # Hold ACK for 5x our idle_detect_time worth of cycles.
            yield from _peer_emit_config(dut.peer, 0x41a0, 200)
            # Then idle.
            yield from _peer_emit_idle(dut.peer, 1000)

        link_seen = []
        restart_count = [0]
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                link_seen.append((yield dut.pcs.link_up))
                if (yield dut.pcs.restart):
                    restart_count[0] += 1
                yield

        self._run(dut, peer(), observe())

        self.assertEqual(link_seen[-1], 1,
            "link did not come up against slow-COMPLETE-ACK peer")

    # ----- Scenario 3: remote fault handling -----

    def test_peer_remote_fault_does_not_link(self):
        """A peer advertising remote fault (RF1 set) must NOT bring link_up.

        Per IEEE 802.3 Clause 37, base page bits 12 (RF1) and 13 (RF2)
        encode a remote-fault condition; any non-zero combination means
        the link must not be declared up. The PCS must:
          - reach RUNNING (the AN handshake itself completes),
          - keep link_up low (because link_rf is set),
          - assert link_rf,
          - NOT restart AN (a restart loop would prevent the peer from
            ever clearing RF without us having to re-handshake).
        """
        dut = _PCSWithPeerDUT()

        # Advertised: FD + pause + RF1 = 0x01A0 | (1<<12) = 0x11A0.
        cfg_no_ack = 0x11a0
        cfg_ack    = cfg_no_ack | (1 << 14)

        def peer():
            yield from _peer_emit_config(dut.peer, cfg_no_ack, 30)
            yield from _peer_emit_config(dut.peer, cfg_ack,    30)

        link_up_in_running = []
        link_rf_in_running = []
        restart_count      = [0]
        states_visited     = set()
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                s = (yield from _read_pcs_state(dut))
                if s:
                    states_visited.add(s)
                if s == "RUNNING":
                    link_up_in_running.append((yield dut.pcs.link_up))
                    link_rf_in_running.append((yield dut.pcs.link_rf))
                if (yield dut.pcs.restart):
                    restart_count[0] += 1
                yield

        self._run(dut, peer(), observe())

        self.assertIn("RUNNING", states_visited,
            f"AN never completed against RF peer; states={sorted(states_visited)}")
        # Once in RUNNING, link_up must stay low and link_rf must be high.
        self.assertGreater(len(link_up_in_running), 0)
        self.assertEqual(max(link_up_in_running), 0,
            "link_up asserted while peer is advertising RF")
        self.assertEqual(min(link_rf_in_running), 1,
            "link_rf was not set despite peer advertising RF")
        # And we must NOT restart AN (otherwise we loop against an RF peer).
        self.assertEqual(restart_count[0], 0,
            f"PCS restarted AN ({restart_count[0]}x) while peer was holding RF")

    def test_peer_clears_rf_link_comes_up(self):
        """Peer holds RF for a while, then clears it: link_up must rise.

        Verifies the asymmetric design choice that RF only suppresses
        link_up without restarting AN - so when the peer drops RF, the
        link comes up immediately without a fresh handshake.
        """
        dut = _PCSWithPeerDUT()

        cfg_rf_no_ack = 0x11a0  # RF1 + FD + pause
        cfg_rf_ack    = cfg_rf_no_ack | (1 << 14)
        cfg_clean     = 0x01a0  # FD + pause, no RF
        cfg_clean_ack = cfg_clean    | (1 << 14)

        def peer():
            # Bring up AN with RF set.
            yield from _peer_emit_config(dut.peer, cfg_rf_no_ack, 30)
            yield from _peer_emit_config(dut.peer, cfg_rf_ack,    30)
            # Hold RF for a while.
            yield from _peer_emit_idle(dut.peer, 200)
            # Clear RF: send fresh consistent config without RF.
            yield from _peer_emit_config(dut.peer, cfg_clean,     30)
            yield from _peer_emit_config(dut.peer, cfg_clean_ack, 30)

        link_up_history = []
        restart_count   = [0]
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                link_up_history.append((yield dut.pcs.link_up))
                if (yield dut.pcs.restart):
                    restart_count[0] += 1
                yield

        self._run(dut, peer(), observe())

        # link_up must transition 0 -> 1 at some point.
        first_up = next(
            (i for i, v in enumerate(link_up_history) if v),
            None,
        )
        self.assertIsNotNone(first_up,
            f"link_up never came up after peer cleared RF; "
            f"restart_count={restart_count[0]}")
        self.assertEqual(link_up_history[-1], 1,
            "link_up did not stay up after peer cleared RF")

    def test_peer_sets_rf_after_link_drops_link_no_restart(self):
        """Link is up; peer then sets RF: link_up must drop, no AN restart."""
        dut = _PCSWithPeerDUT()

        cfg_clean     = 0x01a0
        cfg_clean_ack = cfg_clean | (1 << 14)
        # New consistent config that keeps everything else but adds RF1.
        cfg_rf        = cfg_clean | (1 << 12)

        def peer():
            # Bring link up cleanly.
            yield from _peer_emit_config(dut.peer, cfg_clean,     30)
            yield from _peer_emit_config(dut.peer, cfg_clean_ack, 30)
            yield from _peer_emit_idle(dut.peer, 600)
            # Now flip to RF without going through empty config (no AN
            # restart from the peer's side - just a config change).
            yield from _peer_emit_config(dut.peer, cfg_rf,         30)
            yield from _peer_emit_config(dut.peer, cfg_rf | (1 << 14), 30)

        link_up_history = []
        restart_after_first_up = [0]
        def observe():
            saw_up = False
            for _ in range(self.BUDGET_CYCLES):
                up = (yield dut.pcs.link_up)
                link_up_history.append(up)
                if up:
                    saw_up = True
                if saw_up and (yield dut.pcs.restart):
                    restart_after_first_up[0] += 1
                yield

        self._run(dut, peer(), observe())

        first_up = next((i for i, v in enumerate(link_up_history) if v), None)
        self.assertIsNotNone(first_up, "link never came up at all")
        # After the peer flipped to RF, link_up must end low.
        self.assertEqual(link_up_history[-1], 0,
            "link_up stayed asserted after peer set RF")
        # And we must not have restarted AN as a side-effect of RF.
        self.assertEqual(restart_after_first_up[0], 0,
            f"PCS restarted AN ({restart_after_first_up[0]}x) when peer set RF; "
            f"this would create a restart loop against an RF-holding peer")

    # ----- Scenario 4: peer restarts AN after we have linked up -----

    def test_peer_restart_after_link_triggers_our_restart(self):
        """Peer goes back to empty config after the link is up.

        A 1000BASE-X linkdown is detected when the consistent partner
        config is zero (linkdown.eq(self.lp_abi.o == 0)). The PCS
        should then restart AN.
        """
        dut = _PCSWithPeerDUT()

        def peer():
            # Bring the link up.
            yield from _peer_emit_config(dut.peer, 0x01a0, 30)
            yield from _peer_emit_config(dut.peer, 0x41a0, 30)
            yield from _peer_emit_idle(dut.peer, 600)
            # Now go back to empty config (simulates partner restart).
            yield from _peer_emit_config(dut.peer, 0x0000, 50)
            # Then idle for the rest.
            yield from _peer_emit_idle(dut.peer, 2000)

        ever_up      = [0]
        ever_down    = [0]
        restart_seen = [0]
        def observe():
            saw_up = False
            for _ in range(self.BUDGET_CYCLES):
                if (yield dut.pcs.link_up):
                    ever_up[0]  = 1
                    saw_up      = True
                elif saw_up:
                    ever_down[0] = 1
                if (yield dut.pcs.restart):
                    restart_seen[0] = 1
                yield

        self._run(dut, peer(), observe())

        self.assertEqual(ever_up[0], 1,   "link never came up at all")
        self.assertEqual(ever_down[0], 1, "link did not drop after peer restart")
        self.assertEqual(restart_seen[0], 1,
            "PCS did not pulse `restart` after peer restart")

    # ----- Scenario 5: peer sets NP=1 in advertised config -----

    def test_peer_next_page_does_not_deadlock(self):
        """Peer sets bit 15 (NP). The PCS does not implement next-page
        handling; verify it neither deadlocks nor crashes - the link
        either comes up (NP ignored) or simply stays down (treated as
        an unknown ability), but the FSM must keep running."""
        dut = _PCSWithPeerDUT()

        # Advertised: FD + pause + NP1 = 0x01A0 | (1<<15) = 0x81A0.
        cfg_no_ack = 0x81a0
        cfg_ack    = cfg_no_ack | (1 << 14)

        def peer():
            yield from _peer_emit_config(dut.peer, cfg_no_ack, 30)
            yield from _peer_emit_config(dut.peer, cfg_ack,    30)
            yield from _peer_emit_idle(dut.peer, 1000)
            # Restart AN with non-NP config to confirm we recover.
            yield from _peer_emit_config(dut.peer, 0x01a0, 30)
            yield from _peer_emit_config(dut.peer, 0x41a0, 30)
            yield from _peer_emit_idle(dut.peer, 1000)

        states_visited = set()
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                s = (yield from _read_pcs_state(dut))
                if s:
                    states_visited.add(s)
                yield

        self._run(dut, peer(), observe())

        # Whatever the policy on NP, the FSM must at minimum revisit
        # WAIT-ABI - i.e. it is still alive and not stuck in a single state.
        self.assertIn("AUTONEG-WAIT-ABI", states_visited,
            f"FSM appears stuck; states visited: {sorted(states_visited)}")

    # ----- Scenario 6: peer occasionally injects invalid 10b codes -----

    def test_peer_disparity_error_marks_rx_invalid(self):
        """One bad 10b code must set rx.decoder.invalid in PCS RX.

        We can't easily expose the sticky `rx_invalid` CSR field
        without `add_csr`, so we sample `pcs.rx.decoder.invalid`
        directly across a window after the bad symbol.
        """
        dut = _PCSWithPeerDUT()

        invalid_seen = [0]
        def peer():
            # Settle with idle first.
            yield from _peer_emit_idle(dut.peer, 8)
            # Inject one provably-invalid 10b code.
            #   0b1111111111 has 10 ones, never appears in 8b/10b
            #   (max 8 ones in any valid code).
            yield from _peer_emit_symbol(dut.peer, 0b1111111111, 0, raw=True)
            # Resume idle.
            yield from _peer_emit_idle(dut.peer, 50)

        def observe():
            # Wait long enough for the bad symbol + decoder pipeline.
            for _ in range(80):
                if (yield dut.pcs.rx.decoder.invalid):
                    invalid_seen[0] = 1
                yield

        self._run(dut, peer(), observe())
        self.assertEqual(invalid_seen[0], 1,
            "PCS RX decoder did not flag the invalid 10-bit code")

    # ----- Scenario 7: a single bad symbol does not cause AN restart -----

    def test_peer_isolated_disparity_error_does_not_restart(self):
        """One bad symbol amid valid /C/ traffic should not trigger an
        AN restart - the checker is reset by surrounding /C/ pulses."""
        dut = _PCSWithPeerDUT()

        def peer():
            yield from _peer_emit_config(dut.peer, 0x01a0, 30)
            # One invalid code.
            yield from _peer_emit_symbol(dut.peer, 0b1111111111, 0, raw=True)
            # Continue.
            yield from _peer_emit_config(dut.peer, 0x01a0, 30)
            yield from _peer_emit_config(dut.peer, 0x41a0, 30)
            yield from _peer_emit_idle(dut.peer, 1000)

        restart_count = [0]
        def observe():
            for _ in range(self.BUDGET_CYCLES):
                if (yield dut.pcs.restart):
                    restart_count[0] += 1
                yield

        self._run(dut, peer(), observe())

        # Allow at most one restart (early in BREAKLINK exit it can be
        # racy depending on alignment), but not many.
        self.assertLessEqual(restart_count[0], 1,
            f"single bad symbol caused {restart_count[0]} AN restarts")


if __name__ == "__main__":
    unittest.main()
