#
# This file is part of LiteEth.
#
# Copyright (c) 2025 Fin Maaß <f.maass@vogl-electronic.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.build.io import *

from litex.soc.cores.code_8b10b import K, D, Decoder

from litex.soc.cores.clock.efinix import TITANIUMPLL

from liteeth.common import *
from liteeth.phy.pcs_1000basex import *

# Efinix Serdes Diff TX ----------------------------------------------------------------------------

class EfinixSerdesDiffTx(LiteXModule):
    """
    Differential LVDS transmitter for Efinix Titanium / Topaz.

    Parameters:
    - data     : Parallel data `Signal` to be serialized (LSB first).
    - tx_p     : Positive leg of the LVDS pair (platform pin/record).
    - tx_n     : Negative leg of the LVDS pair (platform pin/record).
    - clk      : “Slow”/parallel clock that presents *data*.
    - fast_clk : “Fast”/serial clock that shifts bits to the pad.
    """
    def __init__(self, data, tx_p, tx_n, clk, fast_clk):
        platform = LiteXContext.platform
        assert platform.family in ("Titanium", "Topaz")

        # Names / Locations.
        # ------------------
        io_name = platform.get_pin_name(tx_p)
        io_pad  = platform.get_pad_name(tx_p)

        # Replace P with PN in io_pad.
        # ----------------------------
        # Split on '_' :  [bank, "P", number, ...]
        io_pad_split = io_pad.split('_')
        if len(io_pad_split) < 3 or io_pad_split[1] != "P":
            raise ValueError(f"Unexpected differential pad name '{io_pad}'")
        # Replace P by PN.
        io_pad = f"{io_pad_split[0]}_PN_{io_pad_split[2]}"

        # Internal signals.
        # -----------------
        _data = platform.add_iface_io(f"{io_name}_gen", len(data))
        _oe   = platform.add_iface_io(f"{io_name}_oe")
        _rst  = platform.add_iface_io(f"{io_name}_rst")
        self.comb += [
            _oe  .eq(1),
            _rst .eq(0),
            _data.eq(data),
        ]

        # LVDS block for Efinity.
        # -----------------------
        platform.toolchain.ifacewriter.blocks.append({
            "type"      : "LVDS",
            "mode"      : "OUTPUT",
            "tx_mode"   : "DATA",
            "name"      : io_name,
            "sig"       : _data,
            "location"  : io_pad,
            "size"      : len(data),
            "slow_clk"  : clk,
            "fast_clk"  : fast_clk,
            "half_rate" : "1",
            "oe"        : _oe,
            "rst"       : _rst,
            "tx_vod"    : "LARGE",
        })
        platform.toolchain.excluded_ios += [tx_p, tx_n] # Mark pads as consumed.

# Efinix Serdes Diff RX ----------------------------------------------------------------------------

class EfinixSerdesDiffRx(LiteXModule):
    """
    Differential LVDS receiver for Efinix Titanium / Topaz.

    Parameters:
    - rx_p     : Positive leg of the LVDS pair (platform pin/record).
    - rx_n     : Negative leg of the LVDS pair (platform pin/record).
    - data     : Parallel data `Signal` where deserialized bits are presented.
    - delay    : Static input delay value (integer, *tap* count expected by Efinity).
    - clk      : “Slow”/parallel clock that captures *data*.
    - fast_clk : “Fast”/serial clock fed to the LVDS deserializer.
    - rx_term  : On‑die termination (``True`` / ``False`` or explicit string `"ON"`, `"OFF"`).
    """
    def __init__(self, rx_p, rx_n, data, delay, clk, fast_clk, rx_term=True):
        platform = LiteXContext.platform
        assert platform.family in ("Titanium", "Topaz")

        # Names / Locations.
        # ------------------
        io_name = platform.get_pin_name(rx_p)
        io_pad  = platform.get_pad_name(rx_p)

        # Replace P with PN in io_pad.
        # ----------------------------
        # Split on '_' :  [bank, "P", number, ...]
        io_pad_split = io_pad.split('_')
        if len(io_pad_split) < 3 or io_pad_split[1] != "P":
            raise ValueError(f"Unexpected differential pad name '{io_pad}'")
        # Replace P by PN.
        io_pad = f"{io_pad_split[0]}_PN_{io_pad_split[2]}"

        # Internal signals.
        # -----------------
        _data = platform.add_iface_io(f"{io_name}_gen", len(data))
        _ena  = platform.add_iface_io(f"{io_name}_ena")
        _rst  = platform.add_iface_io(f"{io_name}_rst")

        self.comb += [
            _ena.eq(1),
            _rst.eq(0),
            data.eq(_data),
        ]

        # LVDS block for Efinity.
        # -----------------------
        platform.toolchain.ifacewriter.blocks.append({
            "type"          : "LVDS",
            "mode"          : "INPUT",
            "rx_mode"       : "NORMAL",
            "name"          : io_name,
            "sig"           : _data,
            "location"      : io_pad,
            "size"          : len(data),
            "slow_clk"      : clk,
            "fast_clk"      : fast_clk,
            "half_rate"     : "1",
            "ena"           : _ena,
            "rst"           : _rst,
            "rx_voc_driver" : "1",
            "rx_term"       : rx_term if isinstance(rx_term, str) else ("ON" if rx_term else "OFF"),
            "rx_delay"      : "STATIC",
            "delay"         : delay,
        })
        platform.toolchain.excluded_ios += [platform.get_pin(rx_p), platform.get_pin(rx_n)]  # Mark pads as consumed.

# Decoder 8b10b Checker ----------------------------------------------------------------------------

class Decoder8b10bChecker(LiteXModule):
    """
    Fast plausibility checker for a 20‑bit word (two 10‑bit symbols).

    * Verifies each symbol’s pop‑count (must contain 4–6 ones).
    * Checks combined running‑disparity window (total ones 9–11).
    * Disallows a comma /K28/ character in the **second** symbol.

    The output *valid* is asserted when **all** criteria pass, meaning the word could be a legally
    encoded 8b/10b lane byte.
    """
    def __init__(self):
        self.data = Signal(20) # i
        self.valid = Signal()  # o

        # # #

        # Symbol popcounts must be 4/5/6 ones.
        sym0_ones = Signal(4)
        sym1_ones = Signal(4)
        self.comb += [
            sym0_ones.eq(Reduce("ADD", self.data[ 0:10])),
            sym1_ones.eq(Reduce("ADD", self.data[10:20])),
        ]
        sym0_bad = (sym0_ones < 4) | (sym0_ones > 6)
        sym1_bad = (sym1_ones < 4) | (sym1_ones > 6)

        # Running disparity window: total ones must be 9/10/11.
        both_ones = Signal(5)
        self.comb += both_ones.eq(sym0_ones + sym1_ones)
        rd_bad = (both_ones < 9) | (both_ones > 11)

        # Forbid comma (K.28) in second symbol.
        sym1_msb  = Cat(*reversed(self.data[10:20])) # bit-reverse
        code6b    = sym1_msb[4:]                # bits 9..4
        comma_bad = (code6b != 0b001111) & (code6b != 0b110000)

        # Set valid when everything is OK.
        self.comb += self.valid.eq(~(sym0_bad | sym1_bad | rd_bad | comma_bad))

# Decoder 8b10b Idle Checker -----------------------------------------------------------------------

class Decoder8b10bIdleChecker(LiteXModule):
    """
    Detects the /I2/ idle ordered set (K28.5 followed by D16.2).

    The two embedded combinatorial decoders analyse the lower and upper 10-bit symbols; *idle* is
    asserted for one cycle when the pair is exactly “K28.5, D16.2” and both symbols are valid.
    """
    def __init__(self):
        self.data = Signal(20) # i
        self.idle = Signal()   # o

        # # #

        # 8b10b Decoders.
        decoders = [Decoder(lsb_first=True, sync=False) for _ in range(2)]
        self.comb += [
            decoders[0].input.eq(self.data[:10]),
            decoders[1].input.eq(self.data[10:]),
        ]
        self.submodules += decoders

        # Idle I2 Check.
        _decoder0_k28_5 = ~decoders[0].invalid &  decoders[0].k & (decoders[0].d == K(28, 5))
        _decoder1_d16_2 = ~decoders[1].invalid & ~decoders[1].k & (decoders[1].d == D(16, 2))
        self.comb += self.idle.eq(_decoder0_k28_5 & _decoder1_d16_2)

# Efinix Aligner -----------------------------------------------------------------------------------

class EfinixAligner(LiteXModule):
    """
    Sliding‑window byte aligner.

    A 30‑bit window (`data`) is scanned by ten overlapping ``Decoder8b10bChecker`` instances
    (bit offsets 0‑9).  When *align* is asserted, the highest offset that passes the checker is
    loaded into *pos*, telling downstream logic how many bits to shift to achieve proper 10‑bit
    boundary alignment.
    """
    def __init__(self):
        self.align = Signal()   # i
        self.data  = Signal(30) # i
        self.shift = Signal(4)  # o

        # # #

        # Create 10 overlapping checkers.
        checkers = []
        for offset in range(10):
            checker = Decoder8b10bChecker()
            checkers.append(checker)
            self.comb += checker.data.eq(self.data[offset:offset + 20])
        self.submodules += checkers

        # Determine shift from highest valid offset.
        for offset in range(10):
              self.sync += If(self.align & checkers[offset].valid, self.shift.eq(offset))

# Efinix Serdes Buffer -----------------------------------------------------------------------------

class EfinixSerdesBuffer(LiteXModule):
    """
    Elastic buffer that:

    * Gathers variable-length slices from the deserializer.
    * Aligns them on a 10-bit boundary (``EfinixAligner``).
    * Strips /I2/ idle ordered-sets when buffer fill is high.
    * Delivers one aligned symbol per cycle when data is ready.

    Parameters:
    - data_in        : Incoming 10-bit slice from the deserializer.
    - data_in_len    : Number of valid bits in *data_in* (1–10).
    - data_out       : Aligned 10-bit symbol delivered to the PCS.
    - data_out_valid : Strobe signalling *data_out* is valid.
    - align          : Pulse requesting the aligner to resynchronise.
    """
    def __init__(self, data_in, data_in_len, data_out, data_out_valid, align):
        # Constants.
        # ----------
        word_bits          = 10                               # Width of one aligned symbol.
        align_window_bits  = 30                               # 10 bits × 3 symbols.
        buffer_bits        = 1200                             # Buffer depth.
        min_accum_bits     = word_bits + align_window_bits    # Minimum for output/align.
        idle_skip_bits     = 20                               # Idle set = 2 × 10-bit symbols.
        min_skip_bits      = min_accum_bits + idle_skip_bits  # Ensure enough after skip.

        # Signals.
        # --------
        buffer           = Signal(buffer_bits)
        pos              = Signal(max=buffer_bits + 1)
        appended_pos     = pos + data_in_len
        masked_data_in   = Signal.like(data_in)
        appended_buffer  = Signal.like(buffer)
        data_out_aligner = Signal(align_window_bits)

        # Aligner.
        # --------
        self.aligner = aligner = EfinixAligner()
        self.comb += [
            aligner.align.eq(align),
            aligner.data.eq(appended_buffer[word_bits:word_bits + align_window_bits])
        ]

        # Idle Checker.
        # -------------
        self.idle_checker = idle_checker = Decoder8b10bIdleChecker()

        # Append Logic (comb, small case for data_in_len).
        # ------------------------------------------------
        self.comb += [
            # Mask input to only include valid bits (9-11 expected).
            Case(data_in_len, {
                9         : masked_data_in.eq(data_in[:9]),
                10        : masked_data_in.eq(data_in[:10]),
                11        : masked_data_in.eq(data_in[:11]),
                "default" : masked_data_in.eq(0), # Fallback, though expecting 9-11.
            }),
            # Append masked data to buffer.
            appended_buffer.eq(buffer | (masked_data_in << pos)),
        ]

        # Aligner Cases for data_out_aligner.
        # -----------------------------------
        cases_aligner = {}
        for i in range(10):
            cases_aligner[i] = data_out_aligner.eq(appended_buffer[i:i+align_window_bits])
        self.comb += [
            idle_checker.data.eq(data_out_aligner[word_bits:]),
            Case(aligner.shift, cases_aligner),
        ]

        # Buffer Update Logic.
        # --------------------
        self.sync += [
            # Not enough bits: accumulate without output.
            If(appended_pos < min_accum_bits,
                data_out.eq(0),
                data_out_valid.eq(0),
                pos.eq(appended_pos),
                buffer.eq(appended_buffer),
            ).Else(
                # Enough bits: output one symbol, optionally skip idle.
                data_out.eq(data_out_aligner[:word_bits]),
                data_out_valid.eq(1),
                # Skip idle if fill is high enough.
                If(idle_checker.idle & (appended_pos >= min_skip_bits),
                    pos.eq(appended_pos - (word_bits + idle_skip_bits)),
                    buffer.eq(appended_buffer >> (word_bits + idle_skip_bits)),
                # Normal: consume one symbol.
                ).Else(
                    pos.eq(appended_pos - word_bits),
                    buffer.eq(appended_buffer >> word_bits),
                ),
            ),
        ]

# Efinix Serdes Diff RX Clock Recovery -------------------------------------------------------------

@ResetInserter()
class EfinixSerdesDiffRxClockRecovery(LiteXModule):
    """
    Clock recovery using multiple phased LVDS receivers for Efinix Titanium / Topaz.

    Parameters:
    - rx_p     : List of 4 positive legs of the LVDS pairs (platform pins/records).
    - rx_n     : List of 4 negative legs of the LVDS pairs (platform pins/records).
    - data     : Output 10-bit deserialized symbol.
    - data_valid : Strobe indicating *data* is valid.
    - align    : Pulse to trigger alignment in the reducer.
    - clk      : Slow/parallel clock domain.
    - fast_clk : Fast/serial clock domain.
    - delay    : List of 4 static delay taps for each receiver.
    - rx_term  : On-die termination (``True`` / ``False`` or "ON"/"OFF").
    - dummy    : Use dummy receivers for simulation/testing.
    """
    def __init__(self, rx_p, rx_n, data, data_valid, align, clk, fast_clk, delay=None, rx_term=True, dummy=False):
        # Assertions.
        # -----------
        assert len(rx_p) == len(rx_n) == 4

        # Constants.
        # ----------
        static_delay = 8  # Base delay per phase offset.
        up_level     = 1  # Threshold for phase up adjustment.
        down_level   = 1  # Threshold for phase down adjustment.

        # Signals.
        # --------
        # Deserializer outputs (20 bits: previous + current 10 bits).
        _data = [Signal(20) for _ in range(4)]

        # Variable-length input to reducer.
        data_buffer     = Signal(11)
        data_buffer_len = Signal(max=12)

        # Sampled data for correlation.
        data_0 = Signal(10)
        data_1 = Signal(10)
        data_2 = Signal(10)
        data_3 = Signal(10)
        data_4 = Signal(10)

        # Corrected data (majority vote).
        data_1_corr = Signal(10)
        data_2_corr = Signal(10)
        data_3_corr = Signal(10)

        # Error signals.
        data_1_eq  = Signal(10)
        data_3_eq  = Signal(10)
        data_1_sum = Signal(max=11)
        data_3_sum = Signal(max=11)

        # Phase decisions.
        up   = Signal()
        down = Signal()

        # Delay adjustments.
        # ------------------
        if delay is None:
            delay = [0] * 4

        # Deserializers.
        # --------------
        for i in range(4):
            data_before = Signal(10)
            if dummy:
                # Dummy receiver for simulation.
                class EfinixSerdesRxDummy(LiteXModule):
                    def __init__(self, data):
                        self.data = Signal(10)
                        self.comb += data.eq(self.data)
                serdesrx = EfinixSerdesRxDummy(_data[i][10:])
            else:
                # Actual LVDS receiver.
                serdesrx = EfinixSerdesDiffRx(
                    rx_p     = rx_p[i],
                    rx_n     = rx_n[i],
                    data     = _data[i][10:],
                    delay    = (static_delay * (3 - i)) + delay[i],
                    clk      = clk,
                    fast_clk = fast_clk,
                    rx_term  = rx_term,
                )
            self.add_module(name=f"serdesrx{i}", module=serdesrx)

            # Latch previous data.
            self.comb += _data[i][:10].eq(data_before)
            self.sync += data_before.eq(_data[i][10:])

        # Reducer (elastic buffer with aligner and idle checker).
        # --------------------------------------------------------
        self.reducer = EfinixSerdesBuffer(data_buffer, data_buffer_len, data, data_valid, align)

        # Majority Vote Corrections.
        # --------------------------
        self.comb += [
            data_1_corr.eq((data_0 & data_1) | (data_2 & data_1) | (data_0 & data_2)),
            data_2_corr.eq((data_1 & data_2) | (data_3 & data_2) | (data_1 & data_3)),
            data_3_corr.eq((data_2 & data_3) | (data_4 & data_3) | (data_2 & data_4)),
        ]

        # Error Detection.
        # ----------------
        self.comb += [
            data_1_eq.eq(data_1_corr ^ data_2_corr),
            data_3_eq.eq(data_3_corr ^ data_2_corr),
            data_1_sum.eq(Reduce("ADD", [data_1_eq[i] for i in range(10)])),
            data_3_sum.eq(Reduce("ADD", [data_3_eq[i] for i in range(10)])),
        ]

        # Phase Decision.
        # ---------------
        self.comb += [
            If((data_1_sum > data_3_sum) & (data_1_sum >= down_level),
                up.eq(1),
            ).Elif((data_1_sum < data_3_sum) & (data_3_sum >= up_level),
                down.eq(1),
            ),
        ]

        # FSM for Phase Selection.
        # ------------------------
        self.fsm = fsm = FSM(reset_state="USE_2")

        fsm.act("USE_0",
            data_0.eq(_data[2][0:10]),
            data_1.eq(_data[3][0:10]),
            data_2.eq(_data[0][1:11]),
            data_3.eq(_data[1][1:11]),
            data_4.eq(_data[2][1:11]),
            If(up,
                NextValue(data_buffer, data_3_corr),
                NextValue(data_buffer_len, 10),
                NextState("USE_1"),
            ).Elif(down,
                NextValue(data_buffer, (_data[2][0:11] & _data[3][0:11]) | (_data[0][1:12] & _data[3][0:11]) | (_data[2][0:11] & _data[0][1:12])),
                NextValue(data_buffer_len, 11),
                NextState("USE_3"),
            ).Else(
                NextValue(data_buffer, data_2_corr),
                NextValue(data_buffer_len, 10),
            )
        )

        fsm.act("USE_1",
            data_0.eq(_data[3][0:10]),
            data_1.eq(_data[0][1:11]),
            data_2.eq(_data[1][1:11]),
            data_3.eq(_data[2][1:11]),
            data_4.eq(_data[3][1:11]),
            NextValue(data_buffer_len, 10),
            If(up,
                NextValue(data_buffer, data_3_corr),
                NextState("USE_2"),
            ).Elif(down,
                NextValue(data_buffer, data_1_corr),
                NextState("USE_0"),
            ).Else(
                NextValue(data_buffer, data_2_corr),
            )
        )

        fsm.act("USE_2",
            data_0.eq(_data[0][1:11]),
            data_1.eq(_data[1][1:11]),
            data_2.eq(_data[2][1:11]),
            data_3.eq(_data[3][1:11]),
            data_4.eq(_data[0][2:12]),
            NextValue(data_buffer_len, 10),
            If(up,
                NextValue(data_buffer, data_3_corr),
                NextState("USE_3"),
            ).Elif(down,
                NextValue(data_buffer, data_1_corr),
                NextState("USE_1"),
            ).Else(
                NextValue(data_buffer, data_2_corr),
            )
        )

        fsm.act("USE_3",
            data_0.eq(_data[1][1:11]),
            data_1.eq(_data[2][1:11]),
            data_2.eq(_data[3][1:11]),
            data_3.eq(_data[0][2:12]),
            data_4.eq(_data[1][2:12]),
            If(up,
                NextValue(data_buffer, data_3_corr[:9]),
                NextValue(data_buffer_len, 9),
                NextState("USE_0"),
            ).Elif(down,
                NextValue(data_buffer, data_1_corr),
                NextValue(data_buffer_len, 10),
                NextState("USE_2"),
            ).Else(
                NextValue(data_buffer, data_2_corr),
                NextValue(data_buffer_len, 10),
            )
        )

# Efinix SerDes Clocking ---------------------------------------------------------------------------

class EfinixSerdesClocking(LiteXModule):
    def __init__(self, refclk, refclk_freq):
        # Parameters.
        # -----------
        platform      = LiteXContext.platform
        fast_clk_freq = 625e6
        clk_freq      = fast_clk_freq / 5

        assert platform.family in ["Titanium", "Topaz"]

        # Slave Mode.
        # -----------

        # Multiply the clock provided by Master with a PLL.

        self.cd_eth_tx       = ClockDomain()
        self.cd_eth_rx       = ClockDomain()
        self.cd_eth_trx_fast = ClockDomain()

        # PLL.
        self.pll = pll = TITANIUMPLL(platform)
        pll.register_clkin(refclk, freq=refclk_freq)

        pll.create_clkout(None,                 refclk_freq)
        pll.create_clkout(self.cd_eth_tx,       clk_freq)
        pll.create_clkout(self.cd_eth_rx,       clk_freq)
        pll.create_clkout(self.cd_eth_trx_fast, fast_clk_freq, phase=90)

        self.comb += pll.reset.eq(ResetSignal("sys"))

# EfinixTitaniumLVDS_1000BASEX PHY -----------------------------------------------------------------

class EfinixTitaniumLVDS_1000BASEX(LiteXModule):
    dw                = 8
    linerate          = 1.25e9
    rx_clk_freq       = 125e6
    tx_clk_freq       = 125e6
    with_preamble_crc = True
    def __init__(self, pads, refclk=None, refclk_freq=200e6, crg=None, rx_delay=None, with_i2c=True, rx_term=True):
        self.pcs = pcs = PCS(lsb_first=True, with_csr=True)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up
        self.ev      = pcs.ev

        # # #

        # Clocking.
        # ---------
        if crg is None:
            assert refclk is not None
            self.crg = EfinixSerdesClocking(
                refclk      = refclk,
                refclk_freq = refclk_freq,
            )
        else:
            self.crg = crg

        # TX.
        # ---
        tx = EfinixSerdesDiffTx(
            data     = pcs.tbi_tx,
            tx_p     = pads.tx_p,
            tx_n     = pads.tx_n,
            clk      = self.crg.cd_eth_tx.clk,
            fast_clk = self.crg.cd_eth_trx_fast.clk,
        )
        self.tx = ClockDomainsRenamer("eth_tx")(tx)


        # RX.
        # ---
        rx = EfinixSerdesDiffRxClockRecovery(
            rx_p       = pads.rx_p,
            rx_n       = pads.rx_n,
            data       = pcs.tbi_rx,
            data_valid = pcs.tbi_rx_ce,
            align      = pcs.align,
            clk        = self.crg.cd_eth_rx.clk,
            fast_clk   = self.crg.cd_eth_trx_fast.clk,
            delay      = rx_delay,
            rx_term    = rx_term,
        )
        self.comb += rx.reset.eq(pcs.restart)
        self.rx = ClockDomainsRenamer("eth_rx")(rx)

        # I2C.
        # ----
        if with_i2c and hasattr(pads, "scl") and hasattr(pads, "sda"):
            from litei2c import LiteI2C

            self.i2c = LiteI2C(LiteXContext.top.sys_clk_freq, pads=pads)
