#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Joel Stanley <jms@oss.tenstorrent.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg, PulseSynchronizer, BusSynchronizer
from migen.genlib.coding import PriorityEncoder
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DifferentialOutput

from litex.soc.interconnect.csr import *
from litex.soc.interconnect import stream

from liteeth.phy.common import LiteEthPHYMDIO
from liteeth.phy.serial.basex.pcs import PCS

from liteeth.phy.serial.basex.pma.us_lvds import (
    COMMA_RD_N,
    COMMA_RD_P,
    USLVDSClocking,
    USLVDSTXGearbox,
    USLVDSSerdesTX,
    USLVDSPhaseDetector,
    USLVDSCommaAligner,
    USLVDSRXGearbox,
    USLVDSSerdesRX,
)

# US LVDS 1000BASE-X PHY ---------------------------------------------------------------------------

class US_LVDS_1000BASEX(LiteXModule):
    """SGMII / 1000BASE-X on differential SelectIO around the generic PCS.

    TX is an 8:1 OSERDESE3. RX is two 1:8 ISERDESE3, the second sampling half a bit later
    through an IDELAYE3, forming an Alexander phase detector that steps the fine phase of the
    RX MMCM outputs: the RX clocks are the recovered clock. The reference is expected to be
    frequency-locked to the link partner (an SGMII PHY providing its clock). With a
    plesiochronous reference the shift wraps once per UI of drift and the continuous comma
    alignment heals the resulting bit slip, but a free-running local 625 MHz reference
    (1000BASE-X over an SFP) is untested.

    Requirements: a 625 MHz reference with a period constraint set by the platform; an
    IDELAYCTRL fed at iodelay_clk_freq and held in reset until crg.locked (UG571 reset order);
    on UltraScale a -2/-3 device (1250 MHz VCO) with the pins in an HR or HP bank; on
    UltraScale+ (usp=True: MMCME4_ADV and ULTRASCALE_PLUS primitives) any speed grade, an HP
    bank and an IDELAYCTRL reference of at least 300 MHz. pcs_kwargs are passed to the PCS,
    e.g. sgmii=True for an SGMII PHY that only completes Auto-Negotiation with SGMII words.

    Unlike the other 1000BASE-X PHYs there is no reset input or _reset CSR: the MMCM is
    sequenced by a free-running power-on counter, never by the sys reset, because resetting it
    stops the IDELAYE3 clocks, drops IDELAYCTRL RDY and thereby resets the SoC.
    """
    dw                = 8
    linerate          = 1.25e9
    rx_clk_freq       = 125e6
    tx_clk_freq       = 125e6
    with_preamble_crc = True
    def __init__(self, pads, refclk_or_clk_pads, sys_clk_freq,
        speedgrade       = -2,
        iodelay_clk_freq = 200e6,
        rx_polarity      = 0,
        tx_polarity      = 0,
        usp              = False,
        pcs_kwargs       = None,
        with_csr         = True,
        hw_reset_cycles  = None):
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        pcs_kwargs.setdefault("with_csr",        with_csr)
        pcs_kwargs.setdefault("sys_clk_freq",    sys_clk_freq)
        self.pcs = pcs = PCS(lsb_first=True, **pcs_kwargs)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up
        if with_csr:
            self.ev = pcs.ev

        # # #

        platform = LiteXContext.platform

        # Clocking.
        # ---------
        self.crg = crg = USLVDSClocking(refclk_or_clk_pads, speedgrade, usp)

        # Power-on sequence.
        # ------------------
        if hw_reset_cycles is None:
            hw_reset_cycles = int(20e-3*sys_clk_freq)
        # Free-running: USIDELAYCTRL holds the sys reset until the IDELAYE3s are clocked, which
        # needs this MMCM locked, which may need the PHY out of reset. PHY reset, then the MMCM
        # reset for another 10 ms; an MMCM unlocked for 10 ms afterwards is reset for 1 ms (UG572).
        mmcm_reset_cycles = hw_reset_cycles + int(10e-3*sys_clk_freq)
        unlock_cycles     = int(10e-3*sys_clk_freq)
        relock_cycles     = int(1e-3*sys_clk_freq)
        por_count       = Signal(max=mmcm_reset_cycles + 1, reset_less=True)
        por_done        = Signal(reset_less=True)
        locked_sys      = Signal(reset_less=True)
        unlock_count    = Signal(max=unlock_cycles + 1, reset_less=True)
        relock_count    = Signal(max=relock_cycles + 1, reset_less=True)
        self.phy_reset  = Signal(reset=1, reset_less=True)
        self.mmcm_reset = Signal(reset=1, reset_less=True)
        self.specials += MultiReg(crg.locked, locked_sys, "sys")
        self.sync += [
            If(~por_done,
                If(por_count != mmcm_reset_cycles, por_count.eq(por_count + 1)),
                If(por_count == hw_reset_cycles,   self.phy_reset.eq(0)),
                If(por_count == mmcm_reset_cycles, self.mmcm_reset.eq(0), por_done.eq(1)),
            ).Elif(relock_count != 0,
                relock_count.eq(relock_count - 1),
                If(relock_count == 1, self.mmcm_reset.eq(0)),
            ).Elif(locked_sys,
                unlock_count.eq(0),
            ).Elif(unlock_count != unlock_cycles,
                unlock_count.eq(unlock_count + 1),
            ).Else(
                unlock_count.eq(0),
                relock_count.eq(relock_cycles),
                self.mmcm_reset.eq(1),
            )
        ]
        # Only this sequence resets the MMCM: a reset drops IDELAYCTRL RDY and resets the SoC.
        self.comb += crg.reset.eq(self.mmcm_reset)
        # The SerDes domains talk to the eth_rx/eth_tx domains through AsyncFIFOs only.
        platform.add_false_path_constraints(crg.cd_eth_rx.clk, crg.cd_eth_rx_div.clk)
        platform.add_false_path_constraints(crg.cd_eth_tx.clk, crg.cd_eth_tx_div.clk)

        # TX.
        # ---
        self.tx = tx = USLVDSSerdesTX(pads, polarity=tx_polarity, usp=usp)
        self.comb += [
            tx.sink.valid.eq(1),
            tx.sink.data.eq(pcs.tbi_tx),
        ]

        # RX.
        # ---
        self.rx = rx = USLVDSSerdesRX(pads, iodelay_clk_freq=iodelay_clk_freq, polarity=rx_polarity,
            usp=usp)
        self.comb += [
            rx.source.ready.eq(1),
            pcs.tbi_rx.eq(rx.source.data),
            pcs.tbi_rx_ce.eq(rx.source.valid),
            crg.psen.eq(rx.pd.psen),
            crg.psincdec.eq(rx.pd.psincdec),
            rx.pd.psdone.eq(crg.psdone),
        ]
        # Continuous alignment: the comma cannot occur at another bit position in a valid 8b/10b
        # stream, and realigning heals the bit slip left by a phase excursion across a transition.
        self.comb += rx.align.eq(1)
        # The PCS restart pulse (eth_tx) is resampled in eth_rx.
        self.restart_sync = restart_sync = PulseSynchronizer("eth_tx", "eth_rx")
        self.comb += [
            restart_sync.i.eq(pcs.restart),
            rx.restart.eq(restart_sync.o),
        ]

        # PHY reset / MDIO.
        # -----------------
        if hasattr(pads, "rst_n"):
            self.comb += pads.rst_n.eq(~self.phy_reset)
        if hasattr(pads, "mdio") and hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)

        # CSRs.
        # -----
        if with_csr:
            self.add_csr()

    def add_csr(self):
        self.cdr_control = CSRStorage(fields=[
            CSRField("enable", size=1, offset=0, reset=1,
                description="Enable the clock recovery loop."),
            CSRField("invert", size=1, offset=1, reset=0,
                description="Invert the phase step direction (debug)."),
            CSRField("inc",    size=1, offset=2, pulse=True,
                description="Manual phase increment step."),
            CSRField("dec",    size=1, offset=3, pulse=True,
                description="Manual phase decrement step."),
        ])
        self.cdr_status = CSRStatus(fields=[
            CSRField("position",  size=6,  offset=0,
                description="Phase shift position (modulo 56 steps)."),
            CSRField("inc_count", size=16, offset=8,
                description="Phase increment steps (wraps)."),
            CSRField("dec_count", size=16, offset=24,
                description="Phase decrement steps (wraps)."),
        ])

        # # #

        pd = self.rx.pd
        self.specials += [
            MultiReg(self.cdr_control.fields.enable, pd.enable, "eth_rx_div"),
            MultiReg(self.cdr_control.fields.invert, pd.invert, "eth_rx_div"),
        ]
        self.step_inc_sync = step_inc_sync = PulseSynchronizer("sys", "eth_rx_div")
        self.step_dec_sync = step_dec_sync = PulseSynchronizer("sys", "eth_rx_div")
        self.status_sync   = status_sync   = BusSynchronizer(38, "eth_rx_div", "sys")
        self.comb += [
            step_inc_sync.i.eq(self.cdr_control.fields.inc),
            step_dec_sync.i.eq(self.cdr_control.fields.dec),
            pd.step_inc.eq(step_inc_sync.o),
            pd.step_dec.eq(step_dec_sync.o),
            status_sync.i.eq(Cat(pd.position, pd.inc_count, pd.dec_count)),
            Cat(self.cdr_status.fields.position,
                self.cdr_status.fields.inc_count,
                self.cdr_status.fields.dec_count).eq(status_sync.o),
        ]
