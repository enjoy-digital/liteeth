#
# This file is part of LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream
from litex.soc.cores.code_8b10b import K, D

from liteeth.common import *
from liteeth.phy.serial.basex.pcs import PCS

_K = K
_D = D
K = lambda x, y: C(_K(x, y), 8)
D = lambda x, y: C(_D(x, y), 8)


# PCS TX -------------------------------------------------------------------------------------------

class PCSTX32(LiteXModule):
    def __init__(self):
        self.config_valid = Signal()                               # Config valid.
        self.config_reg   = Signal(16)                             # Config register (16-bit).
        self.sgmii_speed  = Signal(2)                              # Unused (no SGMII).
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
        self.sgmii_speed     = Signal(2)  # Unused (no SGMII).
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

class PCS32(PCS):
    """1000BASE-X PCS with a 32-bit datapath: shared PCS (Auto-Negotiation, link checker, CSRs)
    with dw=32."""
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
        PCS.__init__(self, lsb_first=lsb_first, dw=32, sgmii=False,
            check_period    = check_period,
            breaklink_time  = breaklink_time,
            more_ack_time   = more_ack_time,
            sgmii_ack_time  = sgmii_ack_time,
            eth_tx_clk_freq = eth_tx_clk_freq,
            with_csr        = with_csr,
            with_autoneg    = with_autoneg,
            sys_clk_freq    = sys_clk_freq,
        )
