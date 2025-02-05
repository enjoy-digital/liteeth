from migen import *

from litex.gen import *

from litex.build.io import *

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
    def __init__(self, rx_p, rx_n, data, static_delay, clk, fast_clk, fifo_clk=None, rx_term=True):
        platform = LiteXContext.platform

        self.rx_fifo_empty = Signal()
        self.rx_fifo_rd = Signal()

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
            "half_rate" : "1",
            "ena"       : _ena,
            "rst"       : _rst,
            "rx_delay"  : "STATIC",
            "delay"     : static_delay,
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


        platform.toolchain.ifacewriter.blocks.append(block)
        platform.toolchain.excluded_ios.append(platform.get_pin(rx_p))
        platform.toolchain.excluded_ios.append(platform.get_pin(rx_n))

class EfinixSerdesDiffRxDynamicDelay(LiteXModule):
    def __init__(self, rx_p, rx_n, data, clk, fast_clk):
        platform = LiteXContext.platform

        self.delay_ena = Signal()
        self.delay_inc = Signal()
        self.delay_rst = Signal()

        # # #

        # Only keep _p.
        io_name = platform.get_pin_name(rx_p)
        io_pad  = platform.get_pad_name(rx_p) # need real pad name
        io_prop = platform.get_pin_properties(rx_p)

        _data = platform.add_iface_io(io_name + "_gen", len(data))
        _ena  = platform.add_iface_io(io_name + "_ena")
        _rst  = platform.add_iface_io(io_name + "_rst")

        assert platform.family in ["Titanium"]
        # _p has _P_ and _n has _N_ followed by an optional function
        # lvds block needs _PN_
        pad_split = io_pad.split('_')
        assert pad_split[1] == 'P'
        io_pad = f"{pad_split[0]}_PN_{pad_split[2]}"
        delay_ena = platform.add_iface_io(io_name + "_delay_ena")
        delay_rst = platform.add_iface_io(io_name + "_delay_rst")
        delay_inc = platform.add_iface_io(io_name + "_delay_inc")

        self.comb += [
            _rst.eq(0),
            _ena.eq(1),
            data.eq(_data),
            delay_inc.eq(self.delay_inc),
            delay_ena.eq(self.delay_ena),
            delay_rst.eq(self.delay_rst),
            ]
        block = {
            "type"      : "LVDS",
            "mode"      : "INPUT",
            "rx_mode"   : "NORMAL",
            "name"      : io_name,
            "sig"       : _data,
            "location"  : io_pad,
            "size"      : len(data),
            "slow_clk"  : ClockSignal(clk),
            "fast_clk"  : ClockSignal(fast_clk),
            "half_rate" : "1",
            "ena_pin"   : _ena,
            "rst_pin"   : _rst,
            "rx_delay"  : "DYNAMIC",
            "delay_ena" : delay_ena,
            "delay_rst" : delay_rst,
            "delay_inc" : delay_inc,
        }

        platform.toolchain.ifacewriter.blocks.append(block)
        platform.toolchain.excluded_ios.append(rx_p)
        platform.toolchain.excluded_ios.append(rx_n)

class EfinixSerdesReducer(LiteXModule):
    def __init__(self, data_in, data_in_len, data_out, data_out_valid):
        
        data_out_buffer_1 = Signal(len(data_out))

        data_out_buffer = Signal(len(data_out)*2)

        buffer_pos = Signal(max=len(data_out)*2)
        
        cases_buffer = {}
        cases_buffer[0] = data_out_buffer.eq(data_in)
        for i in range(1,len(data_in)):
            cases_buffer[i] = data_out_buffer.eq(Cat(data_out_buffer_1[:i], data_in[:len(data_in)-i]))


        self.comb += [
            Case(buffer_pos, cases_buffer)
        ]

        self.sync += [
            If(data_in_len + buffer_pos < len(data_out),
                data_out.eq(0),
                data_out_valid.eq(0),
                buffer_pos.eq(buffer_pos + data_in_len),

                data_out_buffer_1.eq(data_out_buffer[:len(data_out)]),
            ).Else(
                data_out.eq(data_out_buffer[:len(data_out)]),
                data_out_valid.eq(1),
                buffer_pos.eq(buffer_pos + data_in_len - len(data_out)),

                data_out_buffer_1.eq(data_out_buffer[len(data_out):]),
            )
        ]
            

class EfinixSerdesDiffRxClockRecovery(LiteXModule):
    def __init__(self, rx_p, rx_n, data, data_valid, clk, fast_clk, fifo_clk):
        
        assert len(rx_p) == len(rx_n)
        assert len(rx_p) == 4

        _data = Array([Signal(len(data)*2) for i in range(len(rx_p))])

        data_before = Array([Signal(len(data)) for i in range(len(rx_p))])

        _rx_fifo_rd = Signal(reset=1)
        _rx_fifo_empty = Signal()

        data_1_len = Signal(max=12)
        data_0 = Signal(10)
        data_1 = Signal(11)
        data_2 = Signal(10)

        data_0_sum = Signal(max=11)
        data_0_eq = Signal(11)
        data_2_sum = Signal(max=11)
        data_2_eq = Signal(11)

        up_level = 2
        down_level = 2

        up =Signal()
        down = Signal()

        staic_delay = 4

        for i in range(4):
            serdesrx = EfinixSerdesDiffRx(rx_p[i], rx_n[i], _data[i][:len(data)], staic_delay * i, clk, fast_clk, fifo_clk, rx_term=(i == 0))
            self.submodules += serdesrx

            self.comb += [
                serdesrx.rx_fifo_rd.eq(_rx_fifo_rd),
                _rx_fifo_empty.eq(serdesrx.rx_fifo_empty),
                _data[i][len(data):].eq(data_before[i])
            ]

            self.sync += [
                If(_rx_fifo_rd & ~_rx_fifo_empty,
                    data_before[i].eq(_data[i][:len(data)]),
                )
            ]

        self.reducer = EfinixSerdesReducer(data_1, data_1_len, data, data_valid)

        self.comb += [
            data_0_eq.eq(data_0 ^ data_1[:10]),
            data_2_eq.eq(data_2 ^ data_1[:10]),
            data_0_sum.eq(data_0_eq[0] + data_0_eq[1] + data_0_eq[2] + data_0_eq[3] + data_0_eq[4] + data_0_eq[5] + data_0_eq[6] + data_0_eq[7] + data_0_eq[8] + data_0_eq[9] + data_0_eq[10]),
            data_2_sum.eq(data_2_eq[0] + data_2_eq[1] + data_2_eq[2] + data_2_eq[3] + data_2_eq[4] + data_2_eq[5] + data_2_eq[6] + data_2_eq[7] + data_2_eq[8] + data_2_eq[9] + data_2_eq[10]),
            If((data_0_sum > data_2_sum) & (data_0_sum >= down_level),
                up.eq(1),
            ).Elif((data_0_sum < data_2_sum) & (data_2_sum >= up_level),
                down.eq(1),
            )
        ]
        

        self.fsm = fsm = FSM(reset_state="USE_2")

        fsm.act("USE_0",
            If(~_rx_fifo_empty,
                data_1.eq(_data[0][1:11]),
                data_1_len.eq(10),
                data_0.eq(_data[3][0:10]),
                data_2.eq(_data[1][1:11]),
                If(up,
                    NextState("USE_1"),
                ).Elif(down,
                    NextState("0_TO_3"),
                )
            )
        )

        fsm.act("USE_1",
            If(~_rx_fifo_empty,
                data_1.eq(_data[1][1:11]),
                data_1_len.eq(10),
                data_0.eq(_data[0][1:11]),
                data_2.eq(_data[2][1:11]),
                If(up,
                    NextState("USE_2"),
                ).Elif(down,
                    NextState("USE_0"),
                )
            )
        )

        fsm.act("USE_2",
            If(~_rx_fifo_empty,
                data_1.eq(_data[2][1:11]),
                data_1_len.eq(10),
                data_0.eq(_data[1][1:11]),
                data_2.eq(_data[3][1:11]),
                If(up,
                    NextState("USE_3"),
                ).Elif(down,
                    NextState("USE_1"),
                )
            )
        )

        fsm.act("USE_3",
            If(~_rx_fifo_empty,
                data_1.eq(_data[3][1:11]),
                data_1_len.eq(10),
                data_0.eq(_data[2][1:11]),
                data_2.eq(_data[0][2:12]),
                If(up,
                    NextState("3_TO_0"),
                ).Elif(down,
                    NextState("USE_2"),
                )
            )
        )

        fsm.act("3_TO_0",
            If(~_rx_fifo_empty,
                data_1.eq(_data[0][2:11]),
                data_1_len.eq(9),
                NextState("USE_0"),
            )
        )

        fsm.act("0_TO_3",
            If(~_rx_fifo_empty,
                data_1.eq(_data[3][1:12]),
                data_1_len.eq(10),
                NextState("USE_3"),
            )
        )

# Efinix SerDes Clocking ---------------------------------------------------------------------------

class _EfinixSerdesClocking(LiteXModule):
    def __init__(self, refclk, refclk_freq):
        # Parameters.
        # -----------
        platform         = LiteXContext.platform
        fast_clk_freq    = 625e6
        clk_freq = fast_clk_freq / 5

        rx_clk_freq = clk_freq * 1.2

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
        pll.create_clkout(self.cd_eth_rx,              rx_clk_freq)
        pll.create_clkout(self.cd_eth_trx_fast,        fast_clk_freq, phase=90)

class EfinixAlligner(LiteXModule):
    def __init__(self, align, data_out, data_valid_out):
        buffer = Signal(10)
        pos = Signal(max=10)
        self.data_in = data_in = Signal(10)
        self.data_valid_in = data_valid_in = Signal()

        self.sync += [
            If(data_valid_in,
                buffer.eq(data_in),
                data_out.eq(Cat(data_in, buffer) >> pos),
            ),
            If(align,
                If(pos >= 9,
                    pos.eq(0)
                ).Else(
                    pos.eq(pos + 1)
                )
            ),
            data_valid_out.eq(data_valid_in),
        ]

from liteeth.common import *
from liteeth.phy.pcs_1000basex import *

# V7_1000BASEX PHY ---------------------------------------------------------------------------------

class EfinixTitaniumLVDS_1000BASEX(LiteXModule):
    dw          = 8
    linerate    = 1.25e9
    rx_clk_freq = 150e6
    tx_clk_freq = 125e6
    def __init__(self, refclk, pads, refclk_freq=200e6, with_csr=True):
        self.pcs = pcs = PCS(lsb_first=True)

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up

        self.reset = Signal()
        if with_csr:
            self.add_csr()

        # # #

        self.crg = _EfinixSerdesClocking(refclk, refclk_freq)

        self.tx = tx = EfinixSerdesDiffTx(
            pcs.tbi_tx,
            pads.tx_p,
            pads.tx_n,
            self.crg.cd_eth_tx.clk,
            self.crg.cd_eth_trx_fast.clk,
        )

        self.alligner = alligner = ClockDomainsRenamer("eth_rx")(EfinixAlligner(pcs.align, pcs.tbi_rx, pcs.tbi_rx_ce))

        self.rx = rx = ClockDomainsRenamer("eth_rx")(EfinixSerdesDiffRxClockRecovery(
            pads.rx_p,
            pads.rx_n,
            alligner.data_in,
            alligner.data_valid_in,
            self.cd_eth_tx.clk,
            self.cd_eth_trx_fast.clk,
            self.cd_eth_rx.clk,
        ))

    def add_csr(self):
        self._reset = CSRStorage()
        self.comb += self.reset.eq(self._reset.storage)