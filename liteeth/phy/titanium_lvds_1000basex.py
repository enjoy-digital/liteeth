# Copyright (c) 2025 Fin Maaß <f.maass@vogl-electronic.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.build.io import *

from litex.soc.cores.code_8b10b import K, D, DecoderComb

from litex.soc.cores.clock.efinix import TITANIUMPLL


# Efinix Serdes Diff TX ----------------------------------------------------------------------------

class EfinixSerdesDiffTx(LiteXModule):
    def __init__(self, data, tx_p, tx_n, clk, fast_clk):
        platform = LiteXContext.platform
        # only keep _p
        io_name = platform.get_pin_name(tx_p)
        io_pad  = platform.get_pad_name(tx_p) # need real pad name
        io_prop = platform.get_pin_properties(tx_p)

        _data = platform.add_iface_io(io_name + "_gen", len(data))
        _oe   = platform.add_iface_io(io_name + "_oe")
        _rst  = platform.add_iface_io(io_name + "_rst")

        assert platform.family in ["Titanium"]
        # _p has _P_ and _n has _N_ followed by an optional function
        # lvds block needs _PN_
        pad_split = io_pad.split('_')
        assert pad_split[1] == 'P'
        io_pad = f"{pad_split[0]}_PN_{pad_split[2]}"

        self.comb += [
            _data.eq(data),
            _rst.eq(0),
            _oe.eq(1),
        ]

        block = {
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
        }

        platform.toolchain.ifacewriter.blocks.append(block)
        platform.toolchain.excluded_ios.append(tx_p)
        platform.toolchain.excluded_ios.append(tx_n)

# Efinix Serdes Diff RX ----------------------------------------------------------------------------

class EfinixSerdesDiffRx(LiteXModule):
    def __init__(self, rx_p, rx_n, data, delay, clk, fast_clk, fifo_clk=None, rx_term=True, debug=False):
        platform = LiteXContext.platform

        dynamic_delay = bool(delay == "dynamic")
        dpa = bool(delay == "dpa")

        # # #

        # Only keep _p.
        io_name = platform.get_pin_name(rx_p)
        io_pad  = platform.get_pad_name(rx_p) # need real pad name
        io_prop = platform.get_pin_properties(rx_p)

        _data = platform.add_iface_io(io_name + "_gen", len(data))
        _ena  = platform.add_iface_io(io_name + "_ena")
        _rst  = platform.add_iface_io(io_name + "_rst")

        if fifo_clk is not None:
            self.rx_fifo_empty = rx_fifo_empty = platform.add_iface_io(io_name + "_rx_fifo_empty")
            self.rx_fifo_rd = rx_fifo_rd = platform.add_iface_io(io_name + "_rx_fifo_rd")

        if dynamic_delay or dpa:
            self.delay_ena = delay_ena = platform.add_iface_io(io_name + "_delay_ena")
            self.delay_rst = delay_rst = platform.add_iface_io(io_name + "_delay_rst")

            if dynamic_delay:
                self.delay_inc = delay_inc = platform.add_iface_io(io_name + "_delay_inc")
            else:
                self.dpa_dbg  = dpa_dbg = platform.add_iface_io(io_name + "_dpa_dbg", 6)
                self.dpa_lock = dpa_lock = platform.add_iface_io(io_name + "_dpa_lock")

                if debug:
                    self.dpa_debug = dpa_debug = CSRStatus(fields=[
                        CSRField("dpa_dbg", size=6, description="DPA Debug", offset=0),
                        CSRField("dpa_lock", size=1, description="DPA Lock", offset=8),
                    ])

                    self.comb += [
                        dpa_debug.fields.dpa_dbg.eq(dpa_dbg),
                        dpa_debug.fields.dpa_lock.eq(dpa_lock),
                    ]

        assert platform.family in ["Titanium"]
        # _p has _P_ and _n has _N_ followed by an optional function
        # lvds block needs _PN_
        pad_split = io_pad.split('_')
        assert pad_split[1] == 'P'
        io_pad = f"{pad_split[0]}_PN_{pad_split[2]}"

        self.comb += [
            _rst.eq(0),
            _ena.eq(1),
            data.eq(_data),
            ]
        block = {
            "type"      : "LVDS",
            "mode"      : "INPUT",
            "rx_mode"   : "NORMAL",
            "name"      : io_name,
            "sig"       : _data,
            "location"  : io_pad,
            "size"      : len(data),
            "slow_clk"  : clk,
            "fast_clk"  : fast_clk,
            "half_rate" : "1" if not dpa else "0",
            "ena"       : _ena,
            "rst"       : _rst,
            "rx_voc_driver": "1",
            "rx_term"      : rx_term if isinstance(rx_term, str) else ("ON" if rx_term else "OFF"),
        }

        if fifo_clk is not None:
            block.update({
                "rx_fifo"       : True,
                "rx_fifo_empty" : rx_fifo_empty,
                "rx_fifo_rd"    : rx_fifo_rd,
                "rx_fifoclk"    : fifo_clk,
            })

        if dynamic_delay:
            block.update({
                "rx_delay"     : "DYNAMIC",
                "delay_ena"    : delay_ena,
                "delay_rst"    : delay_rst,
                "delay_inc"    : delay_inc,
            })
        elif dpa:
            block.update({
                "rx_delay"     : "DPA",
                "delay_ena"    : delay_ena,
                "delay_rst"    : delay_rst,
                "dpa_dbg"      : dpa_dbg,
                "dpa_lock"     : dpa_lock,
            })
        else:
            block.update({
                "rx_delay"     : "STATIC",
                "delay"        : delay,
            })

        platform.toolchain.ifacewriter.blocks.append(block)
        platform.toolchain.excluded_ios.append(platform.get_pin(rx_p))
        platform.toolchain.excluded_ios.append(platform.get_pin(rx_n))

class EfinixSerdesBuffer(LiteXModule):
    def __init__(self, data_in, data_in_len, data_out, data_out_valid, align):

        self.aligner = aligner = EfinixAligner(align)

        data_out_aligner = Signal(30)
        
        data_out_buffer_1 = Signal(3000)

        data_out_buffer = Signal(len(data_in)+len(data_out_buffer_1))

        buffer_pos = Signal(max=len(data_out_buffer))

        self.idle_remover = Decoder8b10bIdleChecker(data_out_aligner[10:])
        
        cases_buffer = {}
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

class EfinixSerdesDiffRxClockRecovery(LiteXModule):
    def __init__(self, rx_p, rx_n, data, data_valid, align, clk, fast_clk, delay=None):
        
        assert len(rx_p) == len(rx_n)
        assert len(rx_p) == 4

        if delay is None:
            delay = [0, 0, 0, 0]

        _data = Array([Signal(len(data)*2) for i in range(len(rx_p))])

        data_before = Array([Signal(len(data)) for i in range(len(rx_p))])

        data_buffer_len = Signal(max=12)
        data_buffer = Signal(11)
        data_0 = Signal(10)
        data_1 = Signal(10)
        data_2 = Signal(10)

        data_0_sum = Signal(max=11)
        data_0_eq = Signal(10)
        data_2_sum = Signal(max=11)
        data_2_eq = Signal(10)

        data_1_corr = Signal(10)

        up_level = 1
        down_level = 1

        up = Signal()
        down = Signal()

        staic_delay = 8

        for i in range(4):
            serdesrx = EfinixSerdesDiffRx(rx_p[i], rx_n[i], _data[i][10:], (staic_delay * i) + delay[i], clk, fast_clk, rx_term=True) #(i == 0))
            setattr(self, f"serdesrx{i}", serdesrx)

            self.comb += _data[i][:10].eq(data_before[i])

            self.sync += data_before[i].eq(_data[i][10:])


        self.reducer = EfinixSerdesBuffer(data_buffer, data_buffer_len, data, data_valid, align)

        self.comb += [
            data_0_eq.eq(data_0 ^ data_1[:10]),
            data_2_eq.eq(data_2 ^ data_1[:10]),
            data_0_sum.eq(Reduce("ADD", [data_0_eq[i] for i in range(10)])),
            data_2_sum.eq(Reduce("ADD", [data_2_eq[i] for i in range(10)])),
            If((data_0_sum > data_2_sum) & (data_0_sum >= down_level),
                up.eq(1),
            ).Elif((data_0_sum < data_2_sum) & (data_2_sum >= up_level),
                down.eq(1),
            ),

            data_1_corr.eq((data_0 & data_1) | (data_2 & data_1) | (data_0 & data_2)),
        ]
        

        self.fsm = fsm = FSM(reset_state="USE_2")

        fsm.act("USE_0",
            data_1.eq(_data[0][1:11]),
            data_0.eq(_data[3][0:10]),
            data_2.eq(_data[1][1:11]),
            If(up,
                NextValue(data_buffer, data_2),
                NextValue(data_buffer_len, 10),
                NextState("USE_1"),
            ).Elif(down,
                NextValue(data_buffer, _data[3][0:11]),
                NextValue(data_buffer_len, 11),
                NextState("USE_3"),
            ).Else(
                NextValue(data_buffer, data_1_corr),
                NextValue(data_buffer_len, 10),
            )
        )

        fsm.act("USE_1",
            data_1.eq(_data[1][1:11]),
            data_0.eq(_data[0][1:11]),
            data_2.eq(_data[2][1:11]),
            NextValue(data_buffer_len, 10),
            If(up,
                NextValue(data_buffer, data_2),
                NextState("USE_2"),
            ).Elif(down,
                NextValue(data_buffer, data_0),
                NextState("USE_0"),
            ).Else(
                NextValue(data_buffer, data_1_corr),
            )
        )

        fsm.act("USE_2",
            data_1.eq(_data[2][1:11]),
            data_0.eq(_data[1][1:11]),
            data_2.eq(_data[3][1:11]),
            NextValue(data_buffer_len, 10),
            If(up,
                NextValue(data_buffer, data_2),
                NextState("USE_3"),
            ).Elif(down,
                NextValue(data_buffer, data_0),
                NextState("USE_1"),
            ).Else(
                NextValue(data_buffer, data_1_corr),
            )
        )

        fsm.act("USE_3",
            data_1.eq(_data[3][1:11]),
            data_0.eq(_data[2][1:11]),
            data_2.eq(_data[0][2:12]),
            If(up,
                NextValue(data_buffer, _data[0][2:11]),
                NextValue(data_buffer_len, 9),
                NextState("USE_0"),
            ).Elif(down,
                NextValue(data_buffer, data_0),
                NextValue(data_buffer_len, 10),
                NextState("USE_2"),
            ).Else(
                NextValue(data_buffer, data_1_corr),
                NextValue(data_buffer_len, 10),
            )
        )

class EfinixSerdesDiffRxTestDynamic(LiteXModule):
    def __init__(self, rx_p, rx_n, data, clk, fast_clk, rx_term=True):

        data_int = Signal(10)
        data_last = Signal(10)
        data_full = Signal(20)
        data_buffered = Signal(20)
        show = Signal()

        self.serdesrx = serdesrx = EfinixSerdesDiffRx(rx_p, rx_n, data_int, "dynamic", clk, fast_clk, rx_term=rx_term)

        self.match = Signal()

        self.comb += [
            self.match.eq(data_int == data),
            data_full.eq(Cat(data_last, data_int)),
        ]

        self.sync.eth_rx += [
            data_last.eq(data_int),
            show.eq(~show),
            If(show,
               data_buffered.eq(data_full),
            ),
        ]

        self.status = CSRStatus(fields=[
            CSRField("data", size=20, description="Data", offset=0),
        ])

        self.settings = CSRStorage(fields=[
            CSRField("delay", size=6, description="delay", offset=0, reset=31),
        ])

        self.delayset = PulseSynchronizer("sys", "eth_rx")

        self.comb += [
            self.delayset.i.eq(self.settings.re),
            self.status.fields.data.eq(data_buffered),
            serdesrx.delay_rst.eq(ResetSignal("sys")),
        ]

        current_delay = Signal(6)

        fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            If(self.delayset.o,
                serdesrx.delay_rst.eq(1),
                NextValue(current_delay, 31),
                NextState("DELAY")
            )
        )

        fsm.act("DELAY",
            If(current_delay == self.settings.fields.delay,
                NextState("IDLE"),
            ).Elif(current_delay < self.settings.fields.delay,
                serdesrx.delay_ena.eq(1),
                serdesrx.delay_inc.eq(1),
                NextValue(current_delay, current_delay + 1)
            ).Else(
                serdesrx.delay_ena.eq(1),
                serdesrx.delay_inc.eq(0),
                NextValue(current_delay, current_delay - 1)
            )
        )

        self.fsm = ClockDomainsRenamer("eth_rx")(fsm)
            
class EfinixSerdesDiffRxTestDynamicBus(LiteXModule):
    def __init__(self, rx_p, rx_n, data, clk, fast_clk):

        for i in range(4):
            rx = EfinixSerdesDiffRxTestDynamic(rx_p[i], rx_n[i], data, clk, fast_clk, rx_term=True) #(i == 0))
            setattr(self, f"rx{i}", rx)

class EfinixSerdesDiffRxTestDPA(LiteXModule):
    def __init__(self, rx_p, rx_n, clk, fast_clk):

        self.delay_ena = delay_ena = Signal(reset=1)
        self.delay_rst = delay_rst = Signal()

        self.comb += delay_rst.eq(ResetSignal("sys"))

        for i in range(4):
            data_int = Signal(10)
            serdesrx = EfinixSerdesDiffRx(rx_p[i], rx_n[i], data_int, "dpa", clk, fast_clk, rx_term=(i == 0))
            setattr(self, f"serdesrx{i}", serdesrx)

            self.comb += [
                serdesrx.delay_ena.eq(delay_ena),
                serdesrx.delay_rst.eq(delay_rst),
            ]

# Efinix SerDes Clocking ---------------------------------------------------------------------------

class EfinixSerdesClocking(LiteXModule):
    def __init__(self, refclk, refclk_freq):
        # Parameters.
        # -----------
        platform         = LiteXContext.platform
        fast_clk_freq    = 625e6
        clk_freq = fast_clk_freq / 5

        assert platform.family in ["Titanium"]

        # Slave Mode.
        # -----------

        # Multiply the clock provided by Master with a PLL.

        self.cd_eth_tx   = ClockDomain()
        self.cd_eth_rx   = ClockDomain()
        self.cd_eth_trx_fast = ClockDomain()

        # PLL.
        self.pll = pll = TITANIUMPLL(platform)
        pll.register_clkin(refclk, freq=refclk_freq)

        pll.create_clkout(None,                        refclk_freq)
        pll.create_clkout(self.cd_eth_tx,              clk_freq)
        pll.create_clkout(self.cd_eth_rx,              clk_freq)
        pll.create_clkout(self.cd_eth_trx_fast,        fast_clk_freq, phase=90)

        self.comb += pll.reset.eq(ResetSignal("sys"))

class Decoder8b10bChecker(LiteXModule):
    def __init__(self, data_in, valid):

        ones_1 = Signal(4, reset_less=True)
        self.comb += ones_1.eq(Reduce("ADD", [data_in[i] for i in range(10)]))
        invalid_1 = (ones_1 != 4) & (ones_1 != 5) & (ones_1 != 6)

        ones_2 = Signal(4, reset_less=True)
        self.comb += ones_2.eq(Reduce("ADD", [data_in[i] for i in range(10,20)]))
        invalid_2 = (ones_2 != 4) & (ones_2 != 5) & (ones_2 != 6)

        ones_3 = Signal(5, reset_less=True)
        self.comb += ones_3.eq(ones_1 + ones_2)
        invalid_3 = (ones_3 != 9) & (ones_3 != 10) & (ones_3 != 11)

        input_msb_first = Signal(10)
        for i in range(10):
            self.comb += input_msb_first[i].eq(data_in[19-i])

        code6b = input_msb_first[4:]

        invalid_4 = (code6b != 0b001111) & (code6b != 0b110000)
        
        self.comb += valid.eq(~(invalid_1 | invalid_2 | invalid_3 | invalid_4))

class EfinixAligner(LiteXModule):
    def __init__(self, align):
        self.data = data = Signal(30)
        self.pos = pos = Signal(max=10)

        valid_8b10b = Signal(10)

        for i in range(10):
            checker = Decoder8b10bChecker(data[i:20+i], valid_8b10b[i])
            self.submodules += checker

            self.sync += [
                If(align & valid_8b10b[i],
                    pos.eq(i),
                )
            ]

class Decoder8b10bIdleChecker(LiteXModule):
    def __init__(self, data_in):

        self.is_i2 = is_i2 = Signal()

        self.decoder1= decoder1 = DecoderComb(lsb_first=True)
        self.decoder2= decoder2 = DecoderComb(lsb_first=True)

        self.comb += [
            decoder1.input.eq(data_in[:10]),
            decoder2.input.eq(data_in[10:20]),
        ]

        first_ok = decoder1.k & ~decoder1.invalid & (decoder1.d == K(28, 5))
        second_ok = ~decoder2.k & ~decoder2.invalid & (decoder2.d == D(16, 2))
        
        self.comb += is_i2.eq(first_ok & second_ok)

from liteeth.common import *
from liteeth.phy.pcs_1000basex import *

# V7_1000BASEX PHY ---------------------------------------------------------------------------------

class EfinixTitaniumLVDS_1000BASEX(LiteXModule):
    dw          = 8
    linerate    = 1.25e9
    rx_clk_freq = 125e6
    tx_clk_freq = 125e6
    with_preamble_crc = True
    def __init__(self, pads, refclk=None, refclk_freq=200e6, crg=None, rx_delay=None):
        self.pcs = pcs = PCS(lsb_first=True)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up

        # self.data = Signal(10)

        # toogle = Signal()

        # self.storage = CSRStorage(fields=[
        #     CSRField("data", size=20, description="Data", offset=0, reset=0x03FF),
        # ])

        # self.comb += [
        #     If(toogle,
        #         self.data.eq(self.storage.fields.data[0:10]),
        #     ).Else(
        #         self.data.eq(self.storage.fields.data[10:20]),
        #     ),
        # ]

        # self.sync.eth_tx += [
        #     toogle.eq(~toogle),
        # ]
        # # #

        if crg is None:
            assert refclk is not None
            self.crg = EfinixSerdesClocking(refclk, refclk_freq)
        else:
            self.crg = crg

        # self.tx = EfinixSerdesDiffTx(
        #     self.data, # pcs.tbi_tx,
        #     pads.tx_p,
        #     pads.tx_n,
        #     self.crg.cd_eth_tx.clk,
        #     self.crg.cd_eth_trx_fast.clk,
        # )

        self.tx = EfinixSerdesDiffTx(
            pcs.tbi_tx,
            pads.tx_p,
            pads.tx_n,
            self.crg.cd_eth_tx.clk,
            self.crg.cd_eth_trx_fast.clk,
        )

        # self.rx = EfinixSerdesDiffRxTestDynamicBus(
        #     pads.rx_p,
        #     pads.rx_n,
        #     self.data,
        #     self.crg.cd_eth_rx.clk,
        #     self.crg.cd_eth_trx_fast.clk,
        # )

        # self.rx = EfinixSerdesDiffRxTestDPA(
        #     pads.rx_p,
        #     pads.rx_n,
        #     self.crg.cd_eth_rx.clk,
        #     self.crg.cd_eth_trx_fast.clk,
        # )

        rx = EfinixSerdesDiffRxClockRecovery(
            pads.rx_p,
            pads.rx_n,
            pcs.tbi_rx,
            pcs.tbi_rx_valid,
            pcs.align,
            self.crg.cd_eth_rx.clk,
            self.crg.cd_eth_trx_fast.clk,
            delay = rx_delay,
        )

        self.rx = ClockDomainsRenamer("eth_rx")(rx)
