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
    def __init__(self, data_in, valid):
        # Symbol popcounts must be 4/5/6 ones.
        sym0_ones = Signal(4)
        sym1_ones = Signal(4)
        self.comb += [
            sym0_ones.eq(Reduce("ADD", data_in[ 0:10])),
            sym1_ones.eq(Reduce("ADD", data_in[10:20])),
        ]
        sym0_bad = (sym0_ones < 4) | (sym0_ones > 6)
        sym1_bad = (sym1_ones < 4) | (sym1_ones > 6)

        # Running disparity window: total ones must be 9/10/11.
        both_ones = Signal(5)
        self.comb += both_ones.eq(sym0_ones + sym1_ones)
        rd_bad = (both_ones < 9) | (both_ones > 11)

        # Forbid comma (K.28) in second symbol.
        sym1_msb  = Cat(*reversed(data_in[10:20]))   # bit-reverse
        code6b    = sym1_msb[4:]                     # bits 9..4
        comma_bad = (code6b != 0b001111) & (code6b != 0b110000)

        # Output: 1 when everything looks OK
        self.comb += valid.eq(~(sym0_bad | sym1_bad | rd_bad | comma_bad))

# Decoder 8b10b Idle Checker -----------------------------------------------------------------------

class Decoder8b10bIdleChecker(LiteXModule):
    def __init__(self, data_in):
        self.is_i2 = is_i2 = Signal()

        # # #

        self.decoder1 = decoder1 = Decoder(lsb_first=True, sync=False)
        self.decoder2 = decoder2 = Decoder(lsb_first=True, sync=False)

        self.comb += [
            decoder1.input.eq(data_in[:10]),
            decoder2.input.eq(data_in[10:]),
        ]

        first_ok  =  decoder1.k & ~decoder1.invalid & (decoder1.d == K(28, 5))
        second_ok = ~decoder2.k & ~decoder2.invalid & (decoder2.d == D(16, 2))

        self.comb += is_i2.eq(first_ok & second_ok)

# Efinix Aligner -----------------------------------------------------------------------------------

class EfinixAligner(LiteXModule):
    def __init__(self, align):
        self.data = data = Signal(30)
        self.pos  = pos  = Signal(4)

        # # #

        # Create 10 overlapping checkers; highest valid offset wins.
        for off in range(10):
            valid   = Signal()
            checker = Decoder8b10bChecker(data[off:20+off], valid)
            self.submodules += checker
            self.sync += If(align & valid, pos.eq(off))

# Efinix Serdes Diff Rx Dummy ----------------------------------------------------------------------

class EfinixSerdesDiffRxDummy(LiteXModule):
    def __init__(self, data):
        self.data = Signal(10)

        self.comb += data.eq(self.data)

# Efinix Serdes Buffer -----------------------------------------------------------------------------

class EfinixSerdesBuffer(LiteXModule):
    def __init__(self, data_in, data_in_len, data_out, data_out_valid, align):

        self.aligner = aligner = EfinixAligner(align)

        data_out_aligner = Signal(30)

        # It might be possible to use a smaller buffer here.
        # This has to be tested.        
        data_out_buffer_1 = Signal(1000)

        data_out_buffer = Signal(len(data_in)+len(data_out_buffer_1))

        buffer_pos = Signal(max=len(data_out_buffer))

        self.idle_remover = Decoder8b10bIdleChecker(data_out_aligner[10:])
        
        cases_buffer    = {}
        cases_buffer[0] = data_out_buffer.eq(data_in)
        for i in range(1,len(data_out_buffer)):
            cases_buffer[i] = data_out_buffer.eq(Cat(data_out_buffer_1[:i], data_in))

        cases_aligner = {}
        for i in range(10):
            cases_aligner[i] = data_out_aligner.eq(data_out_buffer[i:30+i])

        self.comb += [
            Case(buffer_pos, cases_buffer),
            Case(aligner.pos, cases_aligner),
            aligner.data.eq(data_out_buffer[10:40]),
        ]

        self.sync += [
            If(data_in_len + buffer_pos < 10+30,
                data_out.eq(0),
                data_out_valid.eq(0),
                buffer_pos.eq(buffer_pos + data_in_len),

                data_out_buffer_1.eq(data_out_buffer[:len(data_out_buffer_1)]),
            ).Else(
                data_out.eq(data_out_aligner[:10]),
                data_out_valid.eq(1),
                If(self.idle_remover.is_i2 & (data_in_len + buffer_pos >= 10+30 +20),
                    buffer_pos.eq(buffer_pos + data_in_len - (len(data_out)*3)),

                    data_out_buffer_1.eq(data_out_buffer[len(data_out)*3:]),
                ).Else(
                    buffer_pos.eq(buffer_pos + data_in_len - len(data_out)),

                    data_out_buffer_1.eq(data_out_buffer[len(data_out):]),
                )
            )
        ]

# Efinix Serdes Diff RX Clock Recovery -------------------------------------------------------------

@ResetInserter()
class EfinixSerdesDiffRxClockRecovery(LiteXModule):
    def __init__(self, rx_p, rx_n, data, data_valid, align, clk, fast_clk, delay=None, rx_term=True, dummy=False):
        
        assert len(rx_p) == len(rx_n)
        assert len(rx_p) == 4

        if delay is None:
            delay = [0, 0, 0, 0]

        _data = [Signal(len(data)*2) for _ in range(len(rx_p))]

        data_buffer_len = Signal(max=12)
        data_buffer     = Signal(11)
        data_1          = Signal(10)
        data_2          = Signal(10)
        data_3          = Signal(10)

        data_0          = Signal(10)
        data_4          = Signal(10)

        data_1_sum      = Signal(max=11)
        data_1_eq       = Signal(10)
        data_3_sum      = Signal(max=11)
        data_3_eq       = Signal(10)

        data_1_corr     = Signal(10)
        data_2_corr     = Signal(10)
        data_3_corr     = Signal(10)

        up_level        = 1
        down_level      = 1

        up              = Signal()
        down            = Signal()

        static_delay    = 8

        for i in range(4):
            data_before = Signal(len(data))

            if dummy:
                serdesrx = EfinixSerdesDiffRxDummy(_data[i][10:])
            else:
                serdesrx = EfinixSerdesDiffRx(
                    rx_p     = rx_p[i],
                    rx_n     = rx_n[i],
                    data     = _data[i][10:],
                    delay    = (static_delay * (3-i)) + delay[i],
                    clk      = clk,
                    fast_clk = fast_clk,
                    rx_term  = rx_term,
                )
            self.add_module(name=f"serdesrx{i}", module=serdesrx)

            self.comb += _data[i][:10].eq(data_before)

            self.sync += data_before.eq(_data[i][10:])


        self.reducer = EfinixSerdesBuffer(data_buffer, data_buffer_len, data, data_valid, align)

        self.comb += [
            data_1_eq.eq(data_1_corr ^ data_2_corr),
            data_3_eq.eq(data_3_corr ^ data_2_corr),
            data_1_sum.eq(Reduce("ADD", [data_1_eq[i] for i in range(10)])),
            data_3_sum.eq(Reduce("ADD", [data_3_eq[i] for i in range(10)])),
            If((data_1_sum > data_3_sum) & (data_1_sum >= down_level),
                up.eq(1),
            ).Elif((data_1_sum < data_3_sum) & (data_3_sum >= up_level),
                down.eq(1),
            ),

            data_1_corr.eq((data_0 & data_1) | (data_2 & data_1) | (data_0 & data_2)),
            data_2_corr.eq((data_1 & data_2) | (data_3 & data_2) | (data_1 & data_3)),
            data_3_corr.eq((data_2 & data_3) | (data_4 & data_3) | (data_2 & data_4)),
        ]
        

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
        self.tx = EfinixSerdesDiffTx(
            data     = pcs.tbi_tx,
            tx_p     = pads.tx_p,
            tx_n     = pads.tx_n,
            clk      = self.crg.cd_eth_tx.clk,
            fast_clk = self.crg.cd_eth_trx_fast.clk,
        )

        # RX.
        # ---
        self.rx = ClockDomainsRenamer("eth_rx")(EfinixSerdesDiffRxClockRecovery(
            rx_p       = pads.rx_p,
            rx_n       = pads.rx_n,
            data       = pcs.tbi_rx,
            data_valid = pcs.tbi_rx_ce,
            align      = pcs.align,
            clk        = self.crg.cd_eth_rx.clk,
            fast_clk   = self.crg.cd_eth_trx_fast.clk,
            delay      = rx_delay,
            rx_term    = rx_term,
        ))
        self.comb += self.rx.reset.eq(pcs.restart)

        # I2C.
        # ----
        if with_i2c and hasattr(pads, "scl") and hasattr(pads, "sda"):
            from litei2c import LiteI2C

            self.i2c = LiteI2C(LiteXContext.top.sys_clk_freq, pads=pads)
