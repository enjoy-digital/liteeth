#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Joel Stanley <jms@oss.tenstorrent.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.coding import PriorityEncoder
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DifferentialOutput

from litex.soc.interconnect import stream


# Constants ----------------------------------------------------------------------------------------

# /K28.5/ comma (first 7 bits of the code-group), bit 0 transmitted first.
COMMA_RD_N = 0b1111100 # abcdeif = 0011111.
COMMA_RD_P = 0b0000011 # abcdeif = 1100000.

# US LVDS Clocking ---------------------------------------------------------------------------------

class USLVDSClocking(LiteXModule):
    """MMCM generating the SerDes clocks from the 625 MHz reference.

    - TX: 625 MHz (eth_tx_ser), 156.25 MHz (eth_tx_div), 125 MHz (eth_tx), fixed phase.
    - RX: 625 MHz (eth_rx_ser), 156.25 MHz (eth_rx_div), 125 MHz (eth_rx), shifted
      together through the dynamic phase shift port (psen/psincdec/psdone, clocked
      by eth_rx_div).
    The 156.25 MHz SerDes CLKDIVs are divided from the 625 MHz outputs with BUFGCE_DIV
    so that they meet the ISERDESE3/OSERDESE3 CLK/CLKDIV skew requirement.
    usp selects the UltraScale+ MMCM (MMCME4_ADV, VCO 800 to 1600 MHz on every speed grade).
    """
    def __init__(self, refclk_or_clk_pads, speedgrade=-2, usp=False):
        if not usp and speedgrade not in (-2, -3):
            raise ValueError(f"Unsupported speedgrade {speedgrade}: the 1250 MHz VCO exceeds the "
                             "1200 MHz limit of -1 UltraScale devices, use -2 or -3")
        self.reset  = Signal()
        self.locked = Signal()

        self.psen     = Signal() # i
        self.psincdec = Signal() # i
        self.psdone   = Signal() # o

        self.cd_eth_tx     = ClockDomain()
        self.cd_eth_rx     = ClockDomain()
        self.cd_eth_tx_div = ClockDomain()
        self.cd_eth_rx_div = ClockDomain()
        self.cd_eth_tx_ser = ClockDomain(reset_less=True)
        self.cd_eth_rx_ser = ClockDomain(reset_less=True)

        # # #

        # Reference clock.
        # ----------------
        if isinstance(refclk_or_clk_pads, Signal):
            refclk = refclk_or_clk_pads
        else:
            refclk = Signal()
            self.specials += Instance("IBUFDS",
                i_I  = refclk_or_clk_pads.p,
                i_IB = refclk_or_clk_pads.n,
                o_O  = refclk,
            )

        # MMCM.
        # -----
        mmcm_fb = Signal()
        clkouts = {}
        for name in ["rx_ser", "rx", "tx_ser", "tx"]:
            clkouts[name] = Signal()
        # VCO = 625 MHz / 2 * 4 = 1250 MHz (UltraScale -2/-3 only), PFD at 312.5 MHz.
        # Fine phase step = 1/56 of the VCO period (about 14 ps).
        self.specials += Instance("MMCME4_ADV" if usp else "MMCME3_ADV",
            p_BANDWIDTH          = "OPTIMIZED",
            p_COMPENSATION       = "AUTO",
            p_STARTUP_WAIT       = "FALSE",
            p_REF_JITTER1        = 0.01,
            p_CLKIN1_PERIOD      = 1e9/625e6,
            p_DIVCLK_DIVIDE      = 2,
            p_CLKFBOUT_MULT_F    = 4.0,
            p_CLKFBOUT_PHASE     = 0.0,
            # RX clocks, phase shifted together by the CDR.
            p_CLKOUT0_DIVIDE_F   = 2.0,
            p_CLKOUT0_USE_FINE_PS = "TRUE",
            p_CLKOUT1_DIVIDE     = 10,
            p_CLKOUT1_USE_FINE_PS = "TRUE",
            # TX clocks, fixed phase.
            p_CLKOUT2_DIVIDE     = 2,
            p_CLKOUT3_DIVIDE     = 10,

            i_RST      = self.reset,
            i_PWRDWN   = 0,
            i_CLKINSEL = 1,
            i_CLKIN1   = refclk,
            i_CLKIN2   = 0,
            i_CLKFBIN  = mmcm_fb,
            o_CLKFBOUT = mmcm_fb,
            o_LOCKED   = self.locked,

            i_DADDR    = 0,
            i_DCLK     = 0,
            i_DEN      = 0,
            i_DI       = 0,
            i_DWE      = 0,
            i_CDDCREQ  = 0,

            i_PSCLK    = ClockSignal("eth_rx_div"),
            i_PSEN     = self.psen,
            i_PSINCDEC = self.psincdec,
            o_PSDONE   = self.psdone,

            o_CLKOUT0  = clkouts["rx_ser"],
            o_CLKOUT1  = clkouts["rx"],
            o_CLKOUT2  = clkouts["tx_ser"],
            o_CLKOUT3  = clkouts["tx"],
        )

        # Buffers / Resets.
        # -----------------
        for name, cd in [
            ("rx_ser", self.cd_eth_rx_ser),
            ("rx",     self.cd_eth_rx),
            ("tx_ser", self.cd_eth_tx_ser),
            ("tx",     self.cd_eth_tx)]:
            self.specials += Instance("BUFG", i_I=clkouts[name], o_O=cd.clk)
        for name, cd in [
            ("rx_ser", self.cd_eth_rx_div),
            ("tx_ser", self.cd_eth_tx_div)]:
            self.specials += Instance("BUFGCE_DIV",
                p_BUFGCE_DIVIDE = 4,
                i_I   = clkouts[name],
                i_CE  = 1,
                i_CLR = 0,
                o_O   = cd.clk,
            )
        for cd in [self.cd_eth_rx, self.cd_eth_tx, self.cd_eth_rx_div, self.cd_eth_tx_div]:
            self.specials += AsyncResetSynchronizer(cd, ~self.locked)

# US LVDS TX Gearbox -------------------------------------------------------------------------------

class USLVDSTXGearbox(LiteXModule):
    """10-bit code-groups (eth_tx, 125 MHz) -> 8-bit words (eth_tx_div, 156.25 MHz), bit 0 first.

    10 x 125 MHz and 8 x 156.25 MHz are the same 1.25 Gbps from one MMCM, so the
    AsyncFIFO between the two converters settles to a constant level.
    """
    def __init__(self):
        self.sink   = sink   = stream.Endpoint([("data", 10)])
        self.source = source = stream.Endpoint([("data",  8)])

        # # #

        self.up   = up   = ClockDomainsRenamer("eth_tx")(stream.Converter(10, 40))
        self.cdc  = cdc  = ClockDomainsRenamer({"write": "eth_tx", "read": "eth_tx_div"})(
            stream.AsyncFIFO([("data", 40)], depth=8))
        self.down = down = ClockDomainsRenamer("eth_tx_div")(stream.Converter(40, 8))
        self.comb += [
            sink.connect(up.sink),
            up.source.connect(cdc.sink),
            cdc.source.connect(down.sink),
            down.source.connect(source),
        ]

# US LVDS SerDes TX --------------------------------------------------------------------------------

class USLVDSSerdesTX(LiteXModule):
    """TX gearbox + 8:1 OSERDESE3 (eth_tx_div/eth_tx_ser)."""
    def __init__(self, pads, polarity=0, usp=False):
        self.sink = sink = stream.Endpoint([("data", 10)])

        # # #

        self.gearbox = gearbox = USLVDSTXGearbox()
        self.comb += [
            sink.connect(gearbox.sink),
            gearbox.source.ready.eq(1),
        ]

        # OSERDESE3.
        # ----------
        tx_word = Signal(8)
        tx_data = Signal(8)
        tx_o    = Signal()
        self.comb += [
            # Send zeros while the gearbox has no valid word (start-up) instead of stale data.
            tx_word.eq(Mux(gearbox.source.valid, gearbox.source.data, 0)),
            tx_data.eq(tx_word ^ Replicate(polarity, 8)),
        ]
        self.specials += [
            Instance("OSERDESE3",
                p_DATA_WIDTH         = 8,
                p_INIT               = 0,
                p_IS_CLK_INVERTED    = 0,
                p_IS_CLKDIV_INVERTED = 0,
                p_IS_RST_INVERTED    = 0,
                p_SIM_DEVICE         = "ULTRASCALE_PLUS" if usp else "ULTRASCALE",

                i_RST    = ResetSignal("eth_tx_div"),
                i_CLK    = ClockSignal("eth_tx_ser"),
                i_CLKDIV = ClockSignal("eth_tx_div"),
                i_D      = tx_data,
                i_T      = 0,
                o_OQ     = tx_o,
            ),
            DifferentialOutput(tx_o, pads.tx_p, pads.tx_n),
        ]

# US LVDS Phase Detector ---------------------------------------------------------------------------

class USLVDSPhaseDetector(LiteXModule):
    """Alexander phase detector + MMCM phase shift controller (eth_rx_div domain).

    data/mon are the 8-bit words of the data and monitor ISERDESE3, bit 0 first.
    The monitor sees the line delayed by half a bit period, so monitor bit i is
    sampled at the transition between data bits i-1 and i. On a transition
    d[i-1] != d[i]:
    - m[i] == d[i-1]: the transition is after the monitor sample -> the sampling
                      clock is early -> move it later (phase increment).
    - m[i] == d[i]  : the sampling clock is late -> phase decrement.
    Votes are accumulated and a phase step is requested once |acc| >= threshold.
    """
    def __init__(self, data, mon, threshold=16):
        self.enable   = Signal(reset=1) # i
        self.invert   = Signal()        # i
        self.step_inc = Signal()        # i (manual, pulse)
        self.step_dec = Signal()        # i (manual, pulse)

        self.psen     = Signal() # o
        self.psincdec = Signal() # o
        self.psdone   = Signal() # i

        self.inc_count = Signal(16) # o
        self.dec_count = Signal(16) # o
        self.position  = Signal(6)  # o, phase step modulo 56.
        self.acc       = Signal((8, True)) # o, vote accumulator.

        # # #

        # Votes.
        # ------
        # Transitions between consecutive bits, including the last bit of the previous word.
        data_last = Signal()
        self.sync += data_last.eq(data[7])
        d = Cat(data_last, data) # 9 bits, oldest first: d[i] is the bit before data[i].
        early = Signal(8)
        late  = Signal(8)
        for i in range(8):
            transition = d[i] ^ d[i+1]  # Between data[i-1] and data[i], where mon[i] is sampled.
            self.comb += [
                early[i].eq(transition & (mon[i] == d[i])),
                late[i].eq( transition & (mon[i] != d[i])),
            ]
        early_n = Signal(4)
        late_n  = Signal(4)
        self.sync += [
            early_n.eq(Reduce("ADD", [early[i] for i in range(8)])),
            late_n.eq( Reduce("ADD", [late[i]  for i in range(8)])),
        ]

        # Accumulator (saturating).
        # -------------------------
        acc      = self.acc
        acc_next = Signal((8, True))
        acc_clr  = Signal()
        req_inc  = Signal()
        req_dec  = Signal()
        self.comb += [
            acc_next.eq(acc + early_n - late_n),
            req_inc.eq(acc >=  threshold),
            req_dec.eq(acc <= -threshold),
        ]
        self.sync += [
            If(acc_clr | ~self.enable,
                acc.eq(0)
            ).Elif(acc_next > 127 - 8,
                acc.eq(127 - 8)
            ).Elif(acc_next < -128 + 8,
                acc.eq(-128 + 8)
            ).Else(
                acc.eq(acc_next)
            )
        ]

        # Phase shift FSM.
        # ----------------
        # One PSEN pulse per step, then wait for PSDONE (12 PSCLK cycles).
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.step_inc,
                self.psen.eq(1),
                self.psincdec.eq(1),
                NextState("WAIT")
            ).Elif(self.step_dec,
                self.psen.eq(1),
                self.psincdec.eq(0),
                NextState("WAIT")
            ).Elif(self.enable & (req_inc | req_dec),
                self.psen.eq(1),
                self.psincdec.eq(req_inc ^ self.invert),
                acc_clr.eq(1),
                NextState("WAIT")
            )
        )
        fsm.act("WAIT",
            acc_clr.eq(1), # Discard the votes taken while the phase shifts.
            If(self.psdone,
                NextState("IDLE")
            )
        )

        # Statistics.
        # -----------
        self.sync += [
            If(self.psen,
                If(self.psincdec,
                    self.inc_count.eq(self.inc_count + 1),
                    If(self.position == 55,
                        self.position.eq(0)
                    ).Else(
                        self.position.eq(self.position + 1)
                    )
                ).Else(
                    self.dec_count.eq(self.dec_count + 1),
                    If(self.position == 0,
                        self.position.eq(55)
                    ).Else(
                        self.position.eq(self.position - 1)
                    )
                )
            )
        ]

# US LVDS Comma Aligner ----------------------------------------------------------------------------

class USLVDSCommaAligner(LiteXModule):
    """Aligns a free-running 10-bit stream (bit 0 first) on the /K28.5/ comma.

    While align is asserted, the comma is searched at the 10 possible bit offsets
    of a 20-bit window and the offset is latched; when align is released the
    offset is held. restart clears the offset.
    """
    def __init__(self):
        self.sink    = sink   = stream.Endpoint([("data", 10)])
        self.source  = source = stream.Endpoint([("data", 10)])
        self.align   = Signal()
        self.restart = Signal()
        self.shift   = Signal(4)

        # # #

        prev   = Signal(10)
        window = Signal(20)
        self.comb += [
            sink.ready.eq(1),
            window.eq(Cat(prev, sink.data)),
        ]
        self.sync += If(sink.valid, prev.eq(sink.data))

        # Comma search.
        # -------------
        comma = Signal(10)
        for i in range(10):
            self.comb += comma[i].eq(
                (window[i:i+7] == COMMA_RD_N) |
                (window[i:i+7] == COMMA_RD_P)
            )
        self.comma_enc = comma_enc = PriorityEncoder(10) # Priority to the lowest offset.
        self.comb += comma_enc.i.eq(comma)
        self.sync += [
            # The PCS Auto-Negotiation restart drops the offset until the next comma.
            If(self.restart,
                self.shift.eq(0)
            ).Elif(self.align & sink.valid & ~comma_enc.n,
                self.shift.eq(comma_enc.o)
            )
        ]

        # Output.
        # -------
        cases = {}
        for i in range(10):
            cases[i] = source.data.eq(window[i:i+10])
        self.sync += [
            source.valid.eq(sink.valid),
            Case(self.shift, cases),
        ]

# US LVDS RX Gearbox -------------------------------------------------------------------------------

class USLVDSRXGearbox(LiteXModule):
    """8-bit words (eth_rx_div, 156.25 MHz) -> comma aligned 10-bit code-groups (eth_rx)."""
    def __init__(self):
        self.sink    = sink   = stream.Endpoint([("data",  8)])
        self.source  = source = stream.Endpoint([("data", 10)])
        self.align   = Signal() # i, eth_rx domain.
        self.restart = Signal() # i, eth_rx domain.

        # # #

        self.up   = up   = ClockDomainsRenamer("eth_rx_div")(stream.Converter(8, 40))
        self.cdc  = cdc  = ClockDomainsRenamer({"write": "eth_rx_div", "read": "eth_rx"})(
            stream.AsyncFIFO([("data", 40)], depth=8))
        self.down = down = ClockDomainsRenamer("eth_rx")(stream.Converter(40, 10))
        self.aligner = aligner = ClockDomainsRenamer("eth_rx")(USLVDSCommaAligner())
        self.comb += [
            sink.connect(up.sink),
            up.source.connect(cdc.sink),
            cdc.source.connect(down.sink),
            down.source.connect(aligner.sink),
            aligner.source.connect(source),
            aligner.align.eq(self.align),
            aligner.restart.eq(self.restart),
        ]

# US LVDS SerDes RX --------------------------------------------------------------------------------

class USLVDSSerdesRX(LiteXModule):
    """IBUFDS_DIFF_OUT -> 2 x IDELAYE3/ISERDESE3 -> phase detector, RX gearbox."""
    def __init__(self, pads, iodelay_clk_freq=200e6, polarity=0, usp=False):
        if usp and iodelay_clk_freq < 300e6:
            raise ValueError(f"Unsupported iodelay_clk_freq {iodelay_clk_freq/1e6:.0f} MHz: the "
                             "UltraScale+ IDELAYCTRL reference must be 300 to 800 MHz")
        self.source  = source = stream.Endpoint([("data", 10)]) # eth_rx domain.
        self.align   = Signal() # i, eth_rx domain (level: realign on every comma while set).
        self.restart = Signal() # i, eth_rx domain.

        # # #

        # Input buffers / SerDes (eth_rx_div domain).
        # -------------------------------------------
        data_i, data_d, data_q = Signal(), Signal(), Signal(8)
        mon_i,  mon_d,  mon_q  = Signal(), Signal(), Signal(8)
        self.specials += Instance("IBUFDS_DIFF_OUT",
            i_I  = pads.rx_p,
            i_IB = pads.rx_n,
            o_O  = data_i,
            o_OB = mon_i,
        )
        for i, d, q, delay in [
            (data_i, data_d, data_q,   0), # Data sampler.
            (mon_i,  mon_d,  mon_q,  400), # Monitor sampler, half a bit later.
        ]:
            self.specials += [
                # The IDELAYE3 clock must be the ISERDESE3 CLKDIV (DRC REQP-1742).
                Instance("IDELAYE3",
                    p_CASCADE          = "NONE",
                    p_DELAY_FORMAT     = "TIME",
                    p_DELAY_SRC        = "IDATAIN",
                    p_DELAY_TYPE       = "FIXED",
                    p_DELAY_VALUE      = delay,
                    p_REFCLK_FREQUENCY = iodelay_clk_freq/1e6,
                    p_UPDATE_MODE      = "ASYNC",
                    p_IS_CLK_INVERTED  = 0,
                    p_IS_RST_INVERTED  = 0,
                    p_SIM_DEVICE       = "ULTRASCALE_PLUS" if usp else "ULTRASCALE",

                    i_CLK         = ClockSignal("eth_rx_div"),
                    i_RST         = ResetSignal("eth_rx_div"),
                    i_EN_VTC      = 1,
                    i_CE          = 0,
                    i_INC         = 0,
                    i_LOAD        = 0,
                    i_CNTVALUEIN  = 0,
                    i_CASC_IN     = 0,
                    i_CASC_RETURN = 0,
                    i_DATAIN      = 0,
                    i_IDATAIN     = i,
                    o_DATAOUT     = d,
                ),
                Instance("ISERDESE3",
                    p_DATA_WIDTH        = 8,
                    p_FIFO_ENABLE       = "FALSE",
                    p_FIFO_SYNC_MODE    = "FALSE",
                    p_IS_CLK_INVERTED   = 0,
                    p_IS_CLK_B_INVERTED = 1,
                    p_IS_RST_INVERTED   = 0,
                    p_SIM_DEVICE        = "ULTRASCALE_PLUS" if usp else "ULTRASCALE",

                    i_D          = d,
                    i_RST        = ResetSignal("eth_rx_div"),
                    i_CLK        = ClockSignal("eth_rx_ser"),
                    i_CLK_B      = ClockSignal("eth_rx_ser"), # Locally inverted.
                    i_CLKDIV     = ClockSignal("eth_rx_div"),
                    i_FIFO_RD_CLK = 0,
                    i_FIFO_RD_EN  = 0,
                    o_Q          = q,
                ),
            ]
        data = Signal(8)
        mon  = Signal(8)
        self.sync.eth_rx_div += [
            data.eq( data_q ^ Replicate(polarity, 8)),
            mon.eq( ~mon_q  ^ Replicate(polarity, 8)), # OB is inverted.
        ]

        # Phase detector.
        # ---------------
        self.pd = ClockDomainsRenamer("eth_rx_div")(USLVDSPhaseDetector(data, mon))

        # Gearbox / Comma alignment.
        # --------------------------
        self.gearbox = gearbox = USLVDSRXGearbox()
        self.comb += [
            gearbox.sink.valid.eq(1),
            gearbox.sink.data.eq(data),
            gearbox.source.connect(source),
            gearbox.align.eq(self.align),
            gearbox.restart.eq(self.restart),
        ]
