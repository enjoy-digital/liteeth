#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.fsm import *
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect import stream
from litex.soc.interconnect.csr_eventmanager import *
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.common import *

# PCS Gearbox --------------------------------------------------------------------------------------

class PCSGearbox(LiteXModule):
    def __init__(self):
        self.tx_data      = Signal(10)
        self.tx_data_half = Signal(20)
        self.rx_data_half = Signal(20)
        self.rx_data      = Signal(10)

        # # #

        # TX
        buf = Signal(20)
        self.sync.eth_tx += buf.eq(Cat(buf[10:], self.tx_data))
        self.sync.eth_tx_half += self.tx_data_half.eq(buf)

        # RX
        phase_half       = Signal()
        phase_half_rereg = Signal()
        self.sync.eth_rx_half += phase_half_rereg.eq(phase_half)
        self.sync.eth_rx += [
            If(phase_half == phase_half_rereg,
                self.rx_data.eq(self.rx_data_half[10:])
            ).Else(
                self.rx_data.eq(self.rx_data_half[:10])
            ),
            phase_half.eq(~phase_half),
        ]

# PCS SGMII Timer ----------------------------------------------------------------------------------

SGMII_1000MBPS_SPEED = 0b10
SGMII_100MBPS_SPEED  = 0b01
SGMII_10MBPS_SPEED   = 0b00

class PCSSGMIITimer(LiteXModule):
    def __init__(self, speed):
        self.enable = Signal()
        self.done   = Signal()

        # # #

        count = Signal(max=100)
        self.comb += self.done.eq(count == 0)
        self.sync += [
            count.eq(count - 1),
            If(~self.enable | self.done,
                Case(speed, {
                    SGMII_10MBPS_SPEED   : count.eq(99),
                    SGMII_100MBPS_SPEED  : count.eq(9),
                    SGMII_1000MBPS_SPEED : count.eq(0),
                })
            )
        ]

# PCS TX -------------------------------------------------------------------------------------------

class PCSTX(LiteXModule):
    def __init__(self, lsb_first=False):
        self.config_valid = Signal()                               # Config valid.
        self.config_reg   = Signal(16)                             # Config register (16-bit).
        self.sgmii_speed  = Signal(2)                              # SGMII speed.
        self.sink         = sink = stream.Endpoint([("data", 8)])  # Data input.

        self.encoder = Encoder(lsb_first=lsb_first) # 8b/10b Encoder.

        # Signals.
        # --------
        count  = Signal() # Byte counter for config register.
        parity = Signal() # Parity for /R/ extension.
        ctype  = Signal() # Toggles config type.

        # SGMII Timer.
        # ------------
        self.timer = timer = PCSSGMIITimer(speed=self.sgmii_speed)

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            self.encoder.k[0].eq(1),
            self.encoder.d[0].eq(K(28, 5)),
            # Wait for valid Config.
            If(self.config_valid,
                NextValue(count, 0),
                NextState("CONFIG-D")
            # Wait for valid Data.
            ).Else(
                If(sink.valid,
                    sink.ready.eq(timer.done),
                    self.encoder.d[0].eq(K(27, 7)), # Start-of-packet /S/.
                    NextState("DATA")
                ).Else(
                    NextState("IDLE")
                )
            )
        )
        fsm.act("CONFIG-D",
            # Send Configuration Word.
            Case(ctype, {
                0b0 : self.encoder.d[0].eq(D(21, 5)), # /C1/.
                0b1 : self.encoder.d[0].eq(D( 2, 2)), # /C2/.
            }),
            NextValue(ctype, ~ctype),
            NextState("CONFIG-REG")
        ),
        fsm.act("CONFIG-REG",
            # Send Configuration Register.
            NextValue(count, count + 1),
            Case(count, {
                0 : self.encoder.d[0].eq(self.config_reg[:8]), # LSB.
                1 : self.encoder.d[0].eq(self.config_reg[8:]), # MSB.
            }),
            If(count == (2 - 1), NextState("START"))
        )
        fsm.act("IDLE",
            # Send Idle words and handle disparity.
            Case(self.encoder.disparity[0], {
                0b0 : self.encoder.d[0].eq(D(5, 6)),   # /I1/ (Preserves disparity).
                0b1 : self.encoder.d[0].eq(D(16, 2)),  # /I2/ (Flips disparity).
            }),
            NextState("START")
        )
        fsm.act("DATA",
            # Send Data.
            timer.enable.eq(1),
            sink.ready.eq(timer.done),
            If(sink.valid,
                self.encoder.d[0].eq(sink.data),
            ).Else(
                self.encoder.k[0].eq(1),
                self.encoder.d[0].eq(K(29, 7)), # End-of-frame /T/.
                NextState("CARRIER-EXTEND")
            )
        )
        fsm.act("CARRIER-EXTEND",
            # Extend carrier with /R/ symbols.
            self.encoder.k[0].eq(1),
            self.encoder.d[0].eq(K(23, 7)), # Carrier Extend /R/.
            If(parity,
                NextState("START")
            )
        )
        self.sync += parity.eq(~parity) # Toggle parity for /R/ extension.

# PCS RX -------------------------------------------------------------------------------------------

class PCSRX(LiteXModule):
    def __init__(self, lsb_first=False):
        self.seen_valid_ci   = Signal()   # CI seen.
        self.seen_config_reg = Signal()   # Config seen.
        self.config_reg      = Signal(16) # Config register (16-bit).
        self.sgmii_speed     = Signal(2)  # SGMII speed.
        self.source          = source = stream.Endpoint([("data", 8), ("error", 1)]) # Data output.

        self.decoder = Decoder(lsb_first=lsb_first) # 8b/10b Decoder.

        # # #

        # Signals.
        # --------
        count = Signal() # Byte counter for config register.

        # SGMII Timer.
        # ------------
        self.timer = timer = PCSSGMIITimer(speed=self.sgmii_speed)

        # Buffer.
        # -------
        self.buffer = buffer = stream.Buffer([("data", 8)], pipe_valid=True, pipe_ready=False)
        self.comb += If(timer.done,
            buffer.source.connect(source, omit={"last", "error"}),
            source.last.eq(buffer.source.valid & ~buffer.sink.valid), # Last when next is not valid.
        )

        # FSM.
        # ----
        self.fsm = fsm = FSM()
        fsm.act("START",
            # Wait for a K-character.
            If(self.decoder.k,
                # K-character is Config or Idle K28.5.
                If(self.decoder.d == K(28, 5),
                    NextValue(count, 0),
                    NextState("CONFIG-D-OR-IDLE")
                ),
                # K-character is Start-of-packet /S/.
                If(self.decoder.d == K(27, 7),
                    timer.enable.eq(1),
                    buffer.sink.valid.eq(1),
                    buffer.sink.data.eq(0x55), # First Preamble Byte.
                    NextState("DATA")
                )
            )
        )
        fsm.act("CONFIG-D-OR-IDLE",
            If(~self.decoder.k & ~self.decoder.invalid,
                # Check for Configuration Word.
                If((self.decoder.d == D(21, 5)) | # /C1/.
                   (self.decoder.d == D( 2, 2)),  # /C2/.
                    self.seen_valid_ci.eq(1),
                    NextState("CONFIG-REG")
                ),
                # Check for Idle Word.
                If((self.decoder.d == D( 5, 6)) | # /I1/.
                   (self.decoder.d == D(16, 2)),  # /I2/.
                    self.seen_valid_ci.eq(1),
                    NextState("START")
                )
            ).Else(
                NextState("ERROR"),
            )
        )
        fsm.act("CONFIG-REG",
            If(~self.decoder.k & ~self.decoder.invalid,
                # Receive for Configuration Register.
                NextValue(count, count + 1),
                Case(count, {
                    0b0 : NextValue(self.config_reg[:8], self.decoder.d), # LSB.
                    0b1 : NextValue(self.config_reg[8:], self.decoder.d), # MSB.
                }),
                If(count == (2 - 1),
                    self.seen_config_reg.eq(1),
                    NextState("START")
                )
            ).Else(
                NextState("ERROR"),
            )
        )
        fsm.act("DATA",
            If(~self.decoder.k & ~self.decoder.invalid,
                # Receive Data.
                timer.enable.eq(1),
                buffer.sink.valid.eq(timer.done),
                buffer.sink.data.eq(self.decoder.d),
            ).Elif(self.decoder.k & (self.decoder.d == K(29, 7)) & ~self.decoder.invalid,
                # K-character is End-of-packet /S/.
                NextState("START"),
            ).Else(
                source.error.eq(1),
                source.last.eq(1),
                source.valid.eq(1),
                If(source.ready,
                   NextState("ERROR"),
                )
            )
        )
        fsm.act("ERROR",
            NextState("START")
        )

# PCS ----------------------------------------------------------------------------------------------

class PCS(LiteXModule):
    autocsr_exclude = {"ev"}
    def __init__(self, lsb_first=False, check_period=6e-3, breaklink_time=10e-3, more_ack_time=10e-3, sgmii_ack_time=1.6e-3, with_csr=False):
        self.tx = ClockDomainsRenamer("eth_tx")(PCSTX(lsb_first=lsb_first))
        self.rx = ClockDomainsRenamer("eth_rx")(PCSRX(lsb_first=lsb_first))

        self.tbi_tx = self.tx.encoder.output[0]
        self.tbi_rx = self.rx.decoder.input
        self.tbi_rx_valid = Signal(reset=1)
        self.sink   = stream.Endpoint(eth_phy_description(8))
        self.source = stream.Endpoint(eth_phy_description(8))

        self.link_up = Signal()
        self.restart = Signal()
        self.align   = Signal()

        self.lp_abi = BusSynchronizer(16, "eth_rx", "eth_tx")

        # # #

        # Signals.
        # --------
        config_empty = Signal()
        self.is_sgmii = is_sgmii = Signal()
        linkdown     = Signal()
        autoneg_ack  = Signal()

        # Sink -> TX / RX -> Source.
        self.comb += [
            self.sink.connect(self.tx.sink,     omit={"last_be", "error"}),
            self.rx.source.connect(self.source, omit={"last_be"}),
        ]

        # Pulse Synchronizers.
        # --------------------
        self.seen_valid_ci     = seen_valid_ci     = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_abi = rx_config_reg_abi = PulseSynchronizer("eth_rx", "eth_tx")
        self.rx_config_reg_ack = rx_config_reg_ack = PulseSynchronizer("eth_rx", "eth_tx")
        self.comb += seen_valid_ci.i.eq(self.rx.seen_valid_ci)

        # Timers.
        # -------
        self.breaklink_timer = breaklink_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(breaklink_time * 125e6))
        self.more_ack_timer  = more_ack_timer  = ClockDomainsRenamer("eth_tx")(WaitTimer(more_ack_time  * 125e6))
        self.sgmii_ack_timer = sgmii_ack_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(sgmii_ack_time * 125e6))

        # Checker.
        # --------
        checker_max   = int(check_period*125e6)
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
        self.comb += [
            is_sgmii.eq(self.lp_abi.o[0]),
            # Detect that link is down:
            # - 1000BASE-X : linkup can be inferred by non-empty reg.
            # - SGMII      : linkup is indicated with bit 15.
            If(~is_sgmii,
                linkdown.eq(self.lp_abi.o == 0),
                self.tx.sgmii_speed.eq(0b10),
                self.rx.sgmii_speed.eq(0b10),
            ).Else(
                linkdown.eq(is_sgmii & ~self.lp_abi.o[15]),
                self.tx.sgmii_speed.eq(self.lp_abi.o[10:12]),
                self.rx.sgmii_speed.eq(self.lp_abi.i[10:12]),
            )
        ]

        # TX Config.
        # ----------
        self.comb += [
            If(~config_empty,
                self.tx.config_reg[0].eq(is_sgmii),                     # SGMII: SGMII in-use.
                self.tx.config_reg[5].eq(~is_sgmii),                    # 1000BASE-X: Full-duplex.
                self.tx.config_reg[14].eq(autoneg_ack),                 # SGMII/1000BASE-X: Acknowledge Bit.
            )
        ]

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
            If(checker_tick & checker_error,
                self.restart.eq(1),
                NextState("AUTONEG-BREAKLINK")
            )
        )
        # COMPLETE_ACKNOWLEDGE.
        fsm.act("AUTONEG-SEND-MORE-ACK",
            self.tx.config_valid.eq(1),
            autoneg_ack.eq(1),
            more_ack_timer.wait.eq(~is_sgmii),
            sgmii_ack_timer.wait.eq(is_sgmii),
            If((is_sgmii & sgmii_ack_timer.done) |
                (~is_sgmii & more_ack_timer.done),
                NextState("RUNNING")
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
            If(self.rx.seen_config_reg,
                # Consistency Count/Check.
                rx_config_reg_last.eq(self.rx.config_reg),
                If(self.rx.config_reg != rx_config_reg_last,
                    rx_config_reg_count.eq(8 - 1)
                ).Else(
                    If(rx_config_reg_count != 0,
                        rx_config_reg_count.eq(rx_config_reg_count - 1),
                    )
                ),
                # When RX Config is consistent.
                If(rx_config_reg_count == 0,
                    # Acknowledgement.
                    If(self.rx.config_reg[14],
                        rx_config_reg_ack.i.eq(1),
                    # Ability match.
                    ).Else(
                        rx_config_reg_abi.i.eq(1),
                    )
                ),
                self.lp_abi.i.eq(self.rx.config_reg)
            )
        ]

        if with_csr:
            self.add_csr()

    def add_csr(self):
        self.status = CSRStatus(fields=[
            CSRField("link_up", description="Link is up.", offset=0),
            CSRField("is_sgmii", description="SGMII in-use.", offset=1),
            CSRField("config_reg", size=16, description="config_reg", offset=16),
        ])
        
        self.lp_abi_csr = BusSynchronizer(16, "eth_rx", "sys")

        self.ev           = EventManager()
        self.ev.link      = EventSourceProcess(edge="any")
        self.ev.finalize()

        delay_max=int(LiteXContext.top.sys_clk_freq)
        link_up_delay_counter = Signal(bits_for(delay_max), reset=delay_max)

        self.comb += [
            self.lp_abi_csr.i.eq(self.lp_abi.i),
            self.status.fields.config_reg.eq(self.lp_abi_csr.o)
        ]

        self.sync += [
            self.status.fields.link_up.eq(self.link_up),
            self.status.fields.is_sgmii.eq(self.is_sgmii),
        ]

        self.fsm = fsm = FSM()

        fsm.act("DOWN",
            If(self.link_up,
                NextValue(link_up_delay_counter, link_up_delay_counter.reset),
                NextState("UP")
            )
        )

        fsm.act("UP",
            If(~self.link_up,
                NextState("DOWN"),
            ).Else(
                If(link_up_delay_counter == 0,
                    self.ev.link.trigger.eq(1),
                ).Else(
                    NextValue(link_up_delay_counter, link_up_delay_counter - 1)
                )
            )
        )
