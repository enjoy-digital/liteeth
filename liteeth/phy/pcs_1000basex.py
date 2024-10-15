#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from math import ceil

from migen import *
from migen.genlib.fsm import *
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *
from litex.gen.genlib.misc import WaitTimer
from litex.gen.genlib.cdc import BusSynchronizer

from litex.soc.interconnect import stream
from litex.soc.cores.code_8b10b import K, D, Encoder, Decoder

from liteeth.common import *

# PCS Constants / Helpers --------------------------------------------------------------------------

SGMII_1000MBPS_SPEED = 0b10
SGMII_100MBPS_SPEED  = 0b01
SGMII_10MBPS_SPEED   = 0b00

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

# PCS TX -------------------------------------------------------------------------------------------

class PCSTX(LiteXModule):
    def __init__(self, lsb_first=False):
        self.config_valid = Signal()                               # Config valid.
        self.config_reg   = Signal(16)                             # Config register (16-bit).
        self.sgmii_speed  = Signal(2)                              # SGMII speed.
        self.sink         = sink = stream.Endpoint([("data", 8)])  # Data input.

        self.encoder = Encoder(lsb_first=lsb_first)  # 8b/10b Encoder.

        # Signals.
        # --------
        count  = Signal() # Byte counter for config register.
        parity = Signal() # Parity for /R/ extension.
        ctype  = Signal() # Toggles config type.

        # SGMII Timer.
        # ------------
        timer        = Signal(max=100)
        timer_done   = Signal()
        timer_enable = Signal()
        self.comb += timer_done.eq(timer == 0)
        self.sync += [
            timer.eq(timer - 1),
            If(~timer_enable | timer_done,
                Case(self.sgmii_speed, {
                    SGMII_10MBPS_SPEED   : timer.eq(99),
                    SGMII_100MBPS_SPEED  : timer.eq(9),
                    SGMII_1000MBPS_SPEED : timer.eq(0),
                })
            )
        ]

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
            timer_enable.eq(1),
            sink.ready.eq(timer_done),
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
        self.rx_en     = Signal()
        self.rx_data   = Signal(8)
        self.sample_en = Signal()

        self.seen_valid_ci   = Signal()
        self.seen_config_reg = Signal()
        self.config_reg      = Signal(16)

        self.decoder = Decoder(lsb_first=lsb_first)

        # SGMII Speed Adaptation.
        self.sgmii_speed = Signal(2)

        # # #

        # Signals.
        # --------
        count = Signal() # Byte counter for config register.

        # SGMII Timer.
        # ------------
        timer        = Signal(max=100)
        timer_enable = Signal()
        timer_done   = Signal()
        self.comb += timer_done.eq(timer == 0)
        self.sync += [
            timer.eq(timer - 1),
            If(~timer_enable | timer_done,
                Case(self.sgmii_speed, {
                    SGMII_10MBPS_SPEED   : timer.eq(99),
                    SGMII_100MBPS_SPEED  : timer.eq( 9),
                    SGMII_1000MBPS_SPEED : timer.eq( 0),
                })
            )
        ]

        # Speed adaptation
        self.comb += self.sample_en.eq(self.rx_en & timer_done)

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
                    timer_enable.eq(1),
                    self.rx_en.eq(1),
                    self.rx_data.eq(0x55), # First Preamble Byte.
                    NextState("DATA")
                )
            )
        )
        fsm.act("CONFIG-D-OR-IDLE",
            NextState("ERROR"),
            If(~self.decoder.k,
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
            )
        )
        fsm.act("CONFIG-REG",
            NextState("ERROR"),
            If(~self.decoder.k,
                # Receive for Configuration Register.
                NextState("CONFIG-REG"),
                NextValue(count, count + 1),
                Case(count, {
                    0b0 : NextValue(self.config_reg[:8], self.decoder.d), # LSB.
                    0b1 : NextValue(self.config_reg[8:], self.decoder.d), # MSB.
                }),
                If(count == (2 - 1),
                    self.seen_config_reg.eq(1),
                    NextState("START")
                )
            )
        )
        fsm.act("DATA",
            NextState("START"),
            If(~self.decoder.k,
                # Receive Data.
                timer_enable.eq(1),
                self.rx_en.eq(1),
                self.rx_data.eq(self.decoder.d),
                NextState("DATA")
            )
        )
        fsm.act("ERROR",
            NextState("START")
        )

# PCS ----------------------------------------------------------------------------------------------

# FIXME: Needs similar cleanup than PCSTX/RX.

class PCS(LiteXModule):
    def __init__(self, lsb_first=False, check_period=6e-3, more_ack_time=10e-3):
        self.tx = ClockDomainsRenamer("eth_tx")(PCSTX(lsb_first=lsb_first))
        self.rx = ClockDomainsRenamer("eth_rx")(PCSRX(lsb_first=lsb_first))

        self.tbi_tx = self.tx.encoder.output[0]
        self.tbi_rx = self.rx.decoder.input
        self.sink   = stream.Endpoint(eth_phy_description(8))
        self.source = stream.Endpoint(eth_phy_description(8))

        self.link_up = Signal()
        self.restart = Signal()
        self.align   = Signal()

        self.lp_abi = BusSynchronizer(16, "eth_rx", "eth_tx")

        # # #

        # Sink  -> TX.
        self.comb += self.sink.connect(self.tx.sink, omit={"last_be", "error"})

        # RX -> Source.
        rx_en_d = Signal()
        self.sync.eth_rx += [
            rx_en_d.eq(self.rx.rx_en),
            self.source.valid.eq(self.rx.sample_en),
            self.source.data.eq(self.rx.rx_data),
        ]
        self.comb += self.source.last.eq(~self.rx.rx_en & rx_en_d)

        # Seen Valid Synchronizer.
        seen_valid_ci = PulseSynchronizer("eth_rx", "eth_tx")
        self.submodules += seen_valid_ci
        self.comb += seen_valid_ci.i.eq(self.rx.seen_valid_ci)

        # Checker.
        checker_max_val = ceil(check_period*125e6)
        checker_counter = Signal(max=checker_max_val+1)
        checker_tick = Signal()
        checker_ok = Signal()
        self.sync.eth_tx += [
            checker_tick.eq(0),
            If(checker_counter == 0,
                checker_tick.eq(1),
                checker_counter.eq(checker_max_val)
            ).Else(
                checker_counter.eq(checker_counter-1)
            ),
            If(seen_valid_ci.o, checker_ok.eq(1)),
            If(checker_tick, checker_ok.eq(0))
        ]

        # Control if tx_config_reg should be empty.
        tx_config_empty = Signal()
        # Detections in SGMII mode.
        is_sgmii = Signal()
        linkdown = Signal()
        self.comb += [
            is_sgmii.eq(self.lp_abi.o[0]),
            # Detect that link is down:
            # - 1000BASE-X : linkup can be inferred by non-empty reg.
            # - SGMII      : linkup is indicated with bit 15.
            linkdown.eq((is_sgmii & ~self.lp_abi.o[15]) | (self.lp_abi.o == 0)),
            self.tx.sgmii_speed.eq(Mux(is_sgmii,
                self.lp_abi.o[10:12], 0b10)),
            self.rx.sgmii_speed.eq(Mux(self.lp_abi.i[0],
                self.lp_abi.i[10:12], 0b10))
        ]
        autoneg_ack = Signal()
        self.comb += [
            self.tx.config_reg.eq(Mux(tx_config_empty, 0,
                (is_sgmii)                          | # SGMII: SGMII in-use
                (~is_sgmii << 5)                    | # 1000BASE-X: Full-duplex
                (Mux(is_sgmii,                        # SGMII: Speed
                    self.lp_abi.o[10:12], 0) << 10) |
                (is_sgmii << 12)                    | # SGMII: Full-duplex
                (autoneg_ack << 14)                 | # SGMII/1000BASE-X: Acknowledge Bit
                (is_sgmii & self.link_up)             # SGMII: Link-up
            ))
        ]

        rx_config_reg_abi = PulseSynchronizer("eth_rx", "eth_tx")
        rx_config_reg_ack = PulseSynchronizer("eth_rx", "eth_tx")
        self.submodules += rx_config_reg_abi, rx_config_reg_ack

        self.more_ack_timer = more_ack_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(ceil(more_ack_time*125e6)))
        # SGMII: use 1.6ms link_timer
        self.sgmii_ack_timer = sgmii_ack_timer = ClockDomainsRenamer("eth_tx")(WaitTimer(ceil(1.6e-3*125e6)))

        self.fsm = fsm = ClockDomainsRenamer("eth_tx")(FSM())
        # AN_ENABLE
        fsm.act("AUTONEG_BREAKLINK",
            self.tx.config_valid.eq(1),
            tx_config_empty.eq(1),
            more_ack_timer.wait.eq(1),
            If(more_ack_timer.done,
                NextState("AUTONEG_WAIT_ABI")
            )
        )
        # ABILITY_DETECT
        fsm.act("AUTONEG_WAIT_ABI",
            self.align.eq(1),
            self.tx.config_valid.eq(1),
            If(rx_config_reg_abi.o,
                NextState("AUTONEG_WAIT_ACK")
            ),
            If((checker_tick & ~checker_ok) | rx_config_reg_ack.o,
                self.restart.eq(1),
                NextState("AUTONEG_BREAKLINK")
            )
        )
        # ACKNOWLEDGE_DETECT
        fsm.act("AUTONEG_WAIT_ACK",
            self.tx.config_valid.eq(1),
            autoneg_ack.eq(1),
            If(rx_config_reg_ack.o,
                NextState("AUTONEG_SEND_MORE_ACK")
            ),
            If(checker_tick & ~checker_ok,
                self.restart.eq(1),
                NextState("AUTONEG_BREAKLINK")
            )
        )
        # COMPLETE_ACKNOWLEDGE
        fsm.act("AUTONEG_SEND_MORE_ACK",
            self.tx.config_valid.eq(1),
            autoneg_ack.eq(1),
            more_ack_timer.wait.eq(~is_sgmii),
            sgmii_ack_timer.wait.eq(is_sgmii),
            If((is_sgmii & sgmii_ack_timer.done) |
                (~is_sgmii & more_ack_timer.done),
                NextState("RUNNING")
            ),
            If(checker_tick & ~checker_ok,
                self.restart.eq(1),
                NextState("AUTONEG_BREAKLINK")
            )
        )
        # LINK_OK
        fsm.act("RUNNING",
            self.link_up.eq(1),
            If((checker_tick & ~checker_ok) | linkdown,
                self.restart.eq(1),
                NextState("AUTONEG_BREAKLINK")
            )
        )

        c_counter       = Signal(max=5)
        prev_config_reg = Signal(16)
        self.sync.eth_rx += [
            # Restart consistency counter
            If(self.rx.seen_config_reg,
                c_counter.eq(4)
            ).Elif(c_counter != 0,
                c_counter.eq(c_counter - 1)
            ),

            rx_config_reg_abi.i.eq(0),
            rx_config_reg_ack.i.eq(0),
            If(self.rx.seen_config_reg,
                # Record current config_reg for comparison in the next clock cycle
                prev_config_reg.eq(self.rx.config_reg),
                # Compare consecutive values of config_reg
                If((c_counter == 1) & (prev_config_reg&0xbfff == self.rx.config_reg&0xbfff),
                    # Acknowledgement/Consistency match
                    If(prev_config_reg[14] & self.rx.config_reg[14],
                        rx_config_reg_ack.i.eq(1),
                    )
                    # Ability match
                    .Else(
                        rx_config_reg_abi.i.eq(1),
                    )
                ),
                # Record advertised ability of link partner
                self.lp_abi.i.eq(self.rx.config_reg)
            )
        ]
