#
# This file is part of LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.fsm import *
from migen.genlib.cdc import MultiReg, PulseSynchronizer

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect import stream
from litex.soc.interconnect.csr_eventmanager import *
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.common import *

_K = K
_D = D
K = lambda x, y: C(_K(x, y), 8)
D = lambda x, y: C(_D(x, y), 8)


# PCS TX -------------------------------------------------------------------------------------------

class PCSTX32(LiteXModule):
    def __init__(self):
        self.config_valid = Signal()                               # Config valid.
        self.config_reg   = Signal(16)                             # Config register (16-bit).
        self.sink         = sink = stream.Endpoint(eth_phy_description(32))  # Data input.


        # PHY ports output
        self.char_is_k = Signal(4) # is K output
        self.data = Signal(32) # Data output

        # Signals.
        # --------
        ctype  = Signal() # Toggles config type.
        
        char_is_k = Signal(4)
        data = Signal(32)
        data.attr.add('keep')
        char_is_k.attr.add('keep')
        self.sync += [
                self.data.eq(data),
                self.char_is_k.eq(char_is_k),
                ]

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            NextValue(char_is_k, 0b0001),
            NextValue(data[0:8], K(28, 5)),
            # Wait for valid Config.
            If(self.config_valid,
                NextValue(ctype, ~ctype),
                NextValue(data[8:16], Mux(ctype,
                    D(21, 5), # /C1/.
                    D( 2, 2), # /C2/.
                )),
                NextValue(data[16:32], self.config_reg),
            # Wait for valid Data.
            ).Else(
                sink.ready.eq(1),
                If(sink.valid,
                    NextValue(data, Cat(
                        K(27, 7), # Start-of-packet /S/.
                        sink.data[8:32]
                    )),
                    NextState("DATA")
                ).Else(
                    NextValue(char_is_k, 0b0101),
                    NextValue(data, Cat( # IDLE: /I2/ /I2/, disparity handled by CC_SERDES
                        K(28, 5),
                        D(16, 2),
                        K(28, 5),
                        D(16, 2),
                    )),
                )
            )
        )
        fsm.act("DATA",
            # Send Data.
            sink.ready.eq(1),
            If(sink.last,
                If(sink.be == 0b1111,
                    NextValue(char_is_k, 0b0000),
                    NextValue(data, sink.data),
                    NextState("END-TRAM")
                ),
                If(sink.be == 0b0111,
                    NextValue(char_is_k, 0b1000),
                    NextValue(data, Cat(
                        sink.data[0:24],
                        K(29, 7), # End-of-frame /T/.
                        )),
                    NextState("CARRIER-EXT")
                ),
                If(sink.be == 0b0011,
                    NextValue(char_is_k, 0b1100),
                    NextValue(data, Cat(
                        sink.data[0:16],
                        K(29, 7), # End-of-frame /T/.
                        K(23, 7), # Carrier Extend /R/.
                        )),
                    NextState("START")
                ),
                If(sink.be == 0b0001,
                    NextValue(char_is_k, 0b1110),
                    NextValue(data, Cat(
                        sink.data[0:8],
                        K(29, 7), # End-of-frame /T/.
                        K(23, 7), # Carrier Extend /R/.
                        K(23, 7),
                        )),
                    NextState("START")
                ),
            ).Else(
                NextValue(char_is_k, 0b0000),
                NextValue(data, sink.data),
            ),
        )
        fsm.act('CARRIER-EXT',
            NextValue(char_is_k, 0b0111),
            NextValue(data, Cat(
                K(23, 7), # Carrier Extend /R/.
                K(23, 7),
                K(28, 5), # IDLE, disparity handeled by CC_SERDES
                D(5, 6), 
            )),
            NextState("START")
        )
        fsm.act('END-TRAM',
            NextValue(char_is_k, 0b1111),
            NextValue(data, Cat(
                K(29, 7), # End-of-frame /T/.
                K(23, 7), # Carrier Extend /R/.
                K(23, 7), # Disparity is handled by CC_SERDES
                K(23, 7), 
            )),
            NextState("START")
        )

# PCS RX -------------------------------------------------------------------------------------------

class PCSRX32(LiteXModule):
    def __init__(self):
        self.seen_valid_ci   = Signal()   # CI seen.
        self.seen_config_reg = Signal()   # Config seen.
        self.config_reg      = Signal(16) # Config register (16-bit).
        self.source          = source = stream.Endpoint(eth_phy_description(32)) # Data output.

        # PHY ports inputs
        self.data = Signal(32)
        self.char_is_k = Signal(4)
        self.disparity_err = Signal(4)
        self.table_err = Signal(4)
        

        # # #

        # Signals.
        # --------
        invalid = Signal()

        # Pre Buffer.
        # To ease routing around the CC_SERDES this buffer allow the logic to be placed further away
        # -----------
        data          = Signal.like(self.data)
        char_is_k     = Signal.like(self.char_is_k)
        disparity_err = Signal.like(self.disparity_err)
        table_err     = Signal.like(self.table_err)
        data.attr.add('keep')
        char_is_k.attr.add('keep')
        disparity_err.attr.add('keep')
        table_err.attr.add('keep')
        self.sync += [
            data.eq(self.data),
            char_is_k.eq(self.char_is_k),
            disparity_err.eq(self.disparity_err),
            table_err.eq(self.table_err),
        ]

        # Buffer.
        # -------
        self.buffer = buffer = stream.Buffer(eth_phy_description(32), pipe_valid=True, pipe_ready=False)
        self.comb += [
            buffer.source.connect(source),
            source.last.eq(source.valid & ~buffer.sink.valid),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            NextValue(buffer.sink.valid, 0),
            # Wait for a K-character.
            If(char_is_k == 0x0001,
                # K-character is Config or Idle K28.5.
                If(data[0:8] == K(28, 5),
                    If((data[8:16] == D(21, 5)) | # /C1/.
                       (data[8:16] == D( 2, 2)),  # /C2/.
                        self.seen_valid_ci.eq(1),
                        self.seen_config_reg.eq(1),
                        self.config_reg.eq(data[16:32]),
                    ),
                    # Check for Idle Word.
                    If((data[8:16] == D( 5, 6)) | # /I1/.
                       (data[8:16] == D(16, 2)),  # /I2/.
                        self.seen_valid_ci.eq(1),
                    ),
                ),
                # K-character is Start-of-packet /S/.
                If(data[0:8] == K(27, 7),
                    NextValue(buffer.sink.valid, 1),
                    NextValue(buffer.sink.be, 0b1111),
                    NextValue(buffer.sink.data, Cat(
                        C(0x55, 8), # First Preamble Byte.
                        data[8:32]
                    )),
                    NextState("DATA")
                )
            ),
            If(char_is_k[1:] == 0x001,
               If(data[8:16] == K(28, 5),
                    # Check for Idle Word.
                    If((data[16:24] == D( 5, 6)) | # /I1/.
                       (data[16:24] == D(16, 2)),  # /I2/.
                        self.seen_valid_ci.eq(1),
                    ),
                ),
                # K-character is Start-of-packet /S/.
               If(data[8:16] == K(27, 7),
                    NextValue(buffer.sink.valid, 1),
                    NextValue(buffer.sink.be, 0b1110),
                    NextValue(buffer.sink.data, Cat(
                        C(0, 8), # First Preamble Byte.
                        C(0x55, 8), # First Preamble Byte.
                        data[16:32]
                    )),
                    NextState("DATA")
                )
            ),
            If(char_is_k[2:] == 0x01,
               If(data[16:24] == K(28, 5),
                    # Check for Idle Word.
                    If((data[24:] == D( 5, 6)) | # /I1/.
                       (data[24:] == D(16, 2)),  # /I2/.
                        self.seen_valid_ci.eq(1),
                    ),
                ),
                # K-character is Start-of-packet /S/.
               If(data[16:24] == K(27, 7),
                    NextValue(buffer.sink.valid, 1),
                    NextValue(buffer.sink.be, 0b1100),
                    NextValue(buffer.sink.data, Cat(
                        C(0, 16), # First Preamble Byte.
                        C(0x55, 8), # First Preamble Byte.
                        data[24:32]
                    )),
                    NextState("DATA")
                )
            ),
            If(char_is_k[3:] == 0x1,
               If(data[24:] == K(28, 5),
                    # Check for Idle Word. (next cycle) maybe not usfull
                    self.seen_valid_ci.eq(1),
                ),
                # K-character is Start-of-packet /S/.
               If(data[24:] == K(27, 7),
                    NextValue(buffer.sink.valid, 1),
                    NextValue(buffer.sink.be, 0b1000),
                    NextValue(buffer.sink.data, Cat(
                        C(0, 24), # First Preamble Byte.
                        C(0x55, 8), # First Preamble Byte.
                    )),
                    NextState("DATA")
                )
            ),
        )
        fsm.act("DATA",
            self.seen_valid_ci.eq(1),
            NextValue(buffer.sink.data, data),
            If((char_is_k == 0),
                # Receive Data.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b1111),
            ).Elif((char_is_k[0] == 1) & (data[0:8] == K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 0),
                NextState("START"),
            ).Elif((char_is_k[:2] == 0b0010) & (data[8:16] == K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b0001),
                NextState("START"),
            ).Elif((char_is_k[:3] == 0b0100) & (data[16:24] == K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b0011),
                NextState("START"),
            ).Elif((char_is_k == 0b1000) & (data[24:32] == K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b0111),
                NextState("START"),
            ).Else( # invalid
                NextValue(buffer.sink.valid, 0),
                NextValue(buffer.sink.be, 0b0000),
                NextState("START"),
            ), 
        )

class Realiner32(LiteXModule):
    def __init__(self,):
        self.sink      = stream.Endpoint(eth_phy_description(32))
        self.source    = stream.Endpoint(eth_phy_description(32))

        rem = Signal(32)
        rem_be = Signal(4)

        self.comb += self.sink.ready.eq(1)

        self.fsm = fsm = FSM()
        fsm.act('IDLE',
                NextValue(self.source.valid, 0),
                NextValue(self.source.last, 0),
                If(self.sink.valid,
                   If(self.sink.be == 0b1111,
                      NextValue(self.source.be, 0b1111),
                      NextValue(self.source.data, self.sink.data),
                      NextValue(self.source.valid, 1),
                      NextState('ALIGN_0'),
                    ),
                   If(self.sink.be == 0b1110,
                      NextValue(self.source.be, 0b0000),
                      NextValue(rem, self.sink.data[8:]),
                      NextState('ALIGN_1'),
                    ),
                   If(self.sink.be == 0b1100,
                      NextValue(self.source.be, 0b0000),
                      NextValue(rem, self.sink.data[16:]),
                      NextState('ALIGN_2'),
                    ),
                   If(self.sink.be == 0b1000,
                      NextValue(self.source.be, 0b0000),
                      NextValue(rem, self.sink.data[24:]),
                      NextState('ALIGN_3'),
                    ),
                ),
        )
        fsm.act('ALIGN_0',
                If(self.sink.valid,
                    NextValue(self.source.be, self.sink.be),
                    NextValue(self.source.data, self.sink.data),
                    NextValue(self.source.valid, 1),
                    If(self.sink.last,
                       NextValue(self.source.last, 1),
                       NextState('IDLE'),
                    ),
                ),
            )
        for i in range(1, 4):
            j = i * 8 # for bytes index
            fsm.act(f'ALIGN_{i}',
                    If(self.sink.valid,
                        NextValue(self.source.be, Cat((1 << (4-i)) - 1, self.sink.be[:i])),
                        NextValue(self.source.valid, 1),
                        NextValue(self.source.data, Cat(rem[:-j], self.sink.data[:j])),
                        NextValue(rem, self.sink.data[j:]),
                        If(self.sink.last,
                           If(self.sink.be[i:],
                               NextValue(rem_be, self.sink.be[i:]),
                               NextState(f'END_{i}'),
                           ).Else(
                               NextValue(self.source.last, 1),
                               NextState('IDLE'),
                           )
                        ),
                    ),
                )
            fsm.act(f'END_{i}',
                    NextValue(self.source.be, rem_be),
                    NextValue(self.source.valid, 1),
                    NextValue(self.source.data, rem[:-j]),
                    NextValue(self.source.last, 1),
                    NextState('IDLE'),
                )

            

# PCS ----------------------------------------------------------------------------------------------

class PCS32(LiteXModule):
    autocsr_exclude = {"ev"}
    def __init__(self, lsb_first=False,
        check_period    = 6e-3,
        breaklink_time  = 10e-3,
        more_ack_time   = 10e-3,
        sgmii_ack_time  = 1.6e-3,
        eth_tx_clk_freq = 125e6,
        with_csr        = True,
        with_autoneg    = True,
        sys_clk_freq    = None,
    ):
        self.sys_clk_freq = sys_clk_freq
        self.tx = ClockDomainsRenamer("eth_tx")(PCSTX32())
        self.rx = ClockDomainsRenamer("eth_rx")(PCSRX32())
        self.rx_realiner = ClockDomainsRenamer("eth_rx")(Realiner32())

        self.sink      = stream.Endpoint(eth_phy_description(32))
        self.source    = stream.Endpoint(eth_phy_description(32))

        self.link_up = Signal()
        self.restart = Signal()
        self.align   = Signal()

        self.lp_abi = BusSynchronizer(16, "eth_rx", "eth_tx")

        # # #

        # Signals.
        # --------
        self.config_empty = config_empty = Signal()
        self.is_sgmii     = is_sgmii     = Signal()
        self.linkdown     = linkdown     = Signal()
        self.autoneg_ack  = autoneg_ack  = Signal()

        # Sink -> TX / RX -> Source.
        self.comb += [
            self.sink.connect(self.tx.sink,     omit={"error"}),
            self.rx.source.connect(self.rx_realiner.sink),
            self.rx_realiner.source.connect(self.source),
        ]

        # Pulse Synchronizers.
        # --------------------
        self.seen_valid_ci      = seen_valid_ci      = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_abi  = rx_config_reg_abi  = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_ack  = rx_config_reg_ack  = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_zero = rx_config_reg_zero = PulseSynchronizer("eth_rx", "eth_tx")
        self.comb += seen_valid_ci.i.eq(self.rx.seen_valid_ci)

        # Timers.
        # -------
        self.breaklink_timer = breaklink_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(breaklink_time * eth_tx_clk_freq))
        self.more_ack_timer  = more_ack_timer  = ClockDomainsRenamer("eth_tx")(WaitTimer(more_ack_time  * eth_tx_clk_freq))

        # Checker.
        # --------
        checker_max   = int(check_period*eth_tx_clk_freq)
        checker_count = Signal(max=checker_max + 1)
        checker_tick  = Signal()
        checker_error = Signal()
        self.sync.eth_tx += [
            checker_tick.eq(0),
            If(checker_count == 0,
                checker_tick.eq(1),
                checker_count.eq(checker_max)
            ).Else(
                checker_count.eq(checker_count - 1)
            ),
            If(seen_valid_ci.o, checker_error.eq(0)),
            If(checker_tick,    checker_error.eq(1))
        ]

        # Linkdown/Speed Detection.
        # -------------------------

        # TX Config.
        # ----------
        self.comb += [
            linkdown.eq(self.lp_abi.o == 0),
            If(~config_empty,
                self.tx.config_reg[0].eq(0),                            # No SGMII support
                self.tx.config_reg[5].eq(1),                            # 1000BASE-X: Full-duplex.
                self.tx.config_reg[14].eq(autoneg_ack),                 # SGMII/1000BASE-X: Acknowledge Bit.
            )
        ]

        if with_autoneg:
            # FSM.
            # ----
            self.fsm = fsm = ClockDomainsRenamer("eth_tx")(FSM())
            # AN_ENABLE.
            fsm.act("AUTONEG-BREAKLINK",
                self.tx.config_valid.eq(1),
                config_empty.eq(1),
                breaklink_timer.wait.eq(1),
                If(breaklink_timer.done,
                    NextState("AUTONEG-WAIT-ABI")
                )
            )
            # ABILITY_DETECT.
            fsm.act("AUTONEG-WAIT-ABI",
                self.align.eq(1),
                self.tx.config_valid.eq(1),
                If(rx_config_reg_abi.o,
                    NextState("AUTONEG-WAIT-ACK")
                ),
                If(checker_tick & checker_error,
                    self.restart.eq(1),
                    NextState("AUTONEG-BREAKLINK")
                )
            )
            # ACKNOWLEDGE_DETECT.
            fsm.act("AUTONEG-WAIT-ACK",
                self.tx.config_valid.eq(1),
                autoneg_ack.eq(1),
                If(rx_config_reg_ack.o,
                    NextState("AUTONEG-SEND-MORE-ACK")
                ),
                # Link partner restarted Auto-Negotiation: restart too.
                If(rx_config_reg_zero.o,
                    NextState("AUTONEG-BREAKLINK")
                ),
                If(checker_tick & checker_error,
                    self.restart.eq(1),
                    NextState("AUTONEG-BREAKLINK")
                )
            )
            # COMPLETE_ACKNOWLEDGE.
            fsm.act("AUTONEG-SEND-MORE-ACK",
                self.tx.config_valid.eq(1),
                autoneg_ack.eq(1),
                more_ack_timer.wait.eq(1),
                If(more_ack_timer.done,
                    NextState("RUNNING")
                ),
                # Link partner restarted Auto-Negotiation: restart too.
                If(rx_config_reg_zero.o,
                    NextState("AUTONEG-BREAKLINK")
                ),
                If(checker_tick & checker_error,
                    self.restart.eq(1),
                    NextState("AUTONEG-BREAKLINK")
                )
            )
            # LINK_OK.
            fsm.act("RUNNING",
                self.link_up.eq(~linkdown),
                If((checker_tick & checker_error) | linkdown,
                    self.restart.eq(1),
                    NextState("AUTONEG-BREAKLINK")
                )
            )

            # RX Config (and consistency check).
            # ----------------------------------
            rx_config_reg_count  = Signal(4)
            rx_config_reg_last   = Signal(16)
            self.sync.eth_rx += [
                # Single-cycle pulses (to the PulseSynchronizers).
                rx_config_reg_abi.i.eq(0),
                rx_config_reg_ack.i.eq(0),
                rx_config_reg_zero.i.eq(0),
                If(self.rx.seen_config_reg,
                    # Consistency Count/Check.
                    rx_config_reg_last.eq(self.rx.config_reg),
                    If(self.rx.config_reg != rx_config_reg_last,
                        rx_config_reg_count.eq(8 - 1)
                    ).Else(
                        If(rx_config_reg_count != 0,
                            rx_config_reg_count.eq(rx_config_reg_count - 1),
                        ).Else(
                            # When RX Config is consistent (Clause 37).
                            If(self.rx.config_reg == 0,
                                # Link partner restarting Auto-Negotiation (AN_ENABLE/breaklink).
                                rx_config_reg_zero.i.eq(1),
                            ).Else(
                                # Ability match, regardless of the Acknowledge bit: the link
                                # partner can already be acknowledging.
                                rx_config_reg_abi.i.eq(1),
                                # Acknowledge match.
                                rx_config_reg_ack.i.eq(self.rx.config_reg[14]),
                            )
                        )
                    ),
                    self.lp_abi.i.eq(self.rx.config_reg)
                )
            ]
        else:
            # Fixed-rate link, no Clause 37 autonegotiation.
            self.comb += self.align.eq(1)
            self.sync.eth_tx += [
                # Release RX reset between failed checks so valid code groups can return.
                self.restart.eq(checker_tick & checker_error),
                If(checker_tick,
                    self.link_up.eq(~checker_error),
                ),
            ]

        if with_csr:
            self.add_csr()

    def add_csr(self, sys_clk_freq=None):
        if sys_clk_freq is None:
            sys_clk_freq = self.sys_clk_freq
        if sys_clk_freq is None:
            sys_clk_freq = getattr(LiteXContext.top, "sys_clk_freq", None)
        if sys_clk_freq is None or int(sys_clk_freq) <= 0:
            raise ValueError("A positive sys_clk_freq is required for PCS CSRs.")

        self.status = CSRStatus(fields=[
            CSRField("link_up",    size=1,  offset=0,  description="Link is up."),
            CSRField("is_sgmii",   size=1,  offset=1,  description="SGMII in-use."),
            CSRField("config_reg", size=16, offset=16, description="Link partner ability register."),
        ])

        self.lp_abi_csr = BusSynchronizer(16, "eth_rx", "sys")

        self.ev      = EventManager()
        self.ev.link = EventSourceProcess(edge="any")
        self.ev.finalize()

        self.comb += [
            self.lp_abi_csr.i.eq(self.lp_abi.i),
            self.status.fields.config_reg.eq(self.lp_abi_csr.o),
        ]


        # The PCS status originates in eth_tx. Both software and the event FSM consume
        # synchronized system-domain copies; the link-partner word uses its bus handshake.
        link_up_tx  = Signal()
        is_sgmii_tx = Signal()
        self.sync.eth_tx += [
            link_up_tx.eq(self.link_up),
            is_sgmii_tx.eq(self.is_sgmii),
        ]
        self.specials += [
            MultiReg(link_up_tx,  self.status.fields.link_up),
            MultiReg(is_sgmii_tx, self.status.fields.is_sgmii),
        ]

        self.link_up_timer = link_up_timer = WaitTimer(int(sys_clk_freq))

        self.csr_fsm = fsm = FSM()
        fsm.act("DOWN",
            If(self.status.fields.link_up,
                NextState("UP")
            )
        )
        fsm.act("UP",
            link_up_timer.wait.eq(1),
            self.ev.link.trigger.eq(link_up_timer.done),
            If(~self.status.fields.link_up,
                NextState("DOWN"),
            )
        )
