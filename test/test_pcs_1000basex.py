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
from litex.gen.sim import run_simulation
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


if __name__ == "__main__":
    unittest.main()
