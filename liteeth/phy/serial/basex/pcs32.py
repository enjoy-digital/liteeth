#
# This file is part of LiteEth.
#
# Copyright (c) 2018-2020 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

# 32-bit TX/RX datapaths (hard 8b/10b coding), used by PCS(dw=32) in pcs.py.

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream
from litex.soc.cores.code_8b10b import K, D

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
        self.sgmii_speed  = Signal(2)                              # Unused (no SGMII).
        self.sink         = sink = stream.Endpoint(eth_phy_description(32))  # Data input.


        # PHY ports output
        self.char_is_k = Signal(4, reset_less=True) # is K output
        self.data = Signal(32, reset_less=True) # Data output
        self.data.attr.add('keep')
        self.char_is_k.attr.add('keep')

        # Signals.
        # --------
        ctype  = Signal() # Toggles config type.
        
        char_is_k = Signal(4, reset_less=True)
        data = Signal(32, reset_less=True)
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
                    NextValue(data, Cat( # IDLE, disparity handeled by CC_SERDES
                        K(28, 5),
                        D(5, 6), 
                        K(28, 5),
                        D(5, 6), 
                    )),
                )
            )
        )
        fsm.act("DATA",
            # Send Data.
            sink.ready.eq(1),
            NextValue(data, sink.data),
            NextValue(char_is_k, 0b0000),
            If(sink.last,
                If(sink.be == 0b1111,
                    NextValue(char_is_k, 0b0000),
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
        data          = Signal.like(self.data,          reset_less=True)
        char_is_k     = Signal.like(self.char_is_k,     reset_less=True)
        disparity_err = Signal.like(self.disparity_err, reset_less=True)
        table_err     = Signal.like(self.table_err,     reset_less=True)
        data.attr.add('keep')
        char_is_k.attr.add('keep')
        self.sync += [
            data.eq(self.data),
            char_is_k.eq(self.char_is_k),
            disparity_err.eq(self.disparity_err),
            table_err.eq(self.table_err),
        ]

        # Decode.
        # Second register stage with the byte comparisons pre-computed, to keep the FSM shallow.
        # -------
        #_data      = data
        #_char_is_k = char_is_k
        #data       = Signal.like(_data,      reset_less=True)
        #char_is_k  = Signal.like(_char_is_k, reset_less=True)
        #self.sync += [
        #    data.eq(_data),
        #    char_is_k.eq(_char_is_k),
        #]
        matches = {}
        def match(lane, code):
            key = (lane, code.value)
            if key not in matches:
                matches[key] = Signal(reset_less=True)
                #self.sync += matches[key].eq(_data[8*lane:8*(lane + 1)] == code)
                self.comb += matches[key].eq(data[8*lane:8*(lane + 1)] == code)
            return matches[key]

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
            NextValue(buffer.sink.data, data),
            NextValue(buffer.sink.be, 0b0000),
            # Wait for a K-character.
            If(char_is_k == 1,
                # K-character is Config or Idle K28.5.
                If(match(0, K(28, 5)),
                    If(match(1, D(21, 5)) | # /C1/.
                       match(1, D( 2, 2)),  # /C2/.
                        self.seen_valid_ci.eq(1),
                        self.seen_config_reg.eq(1),
                        self.config_reg.eq(data[16:32]),
                    ),
                ),
            ),
            *(If(char_is_k[i:] == 1,
                # K-character is Start-of-packet /S/.
                If(match(i, K(27, 7)),
                    NextValue(buffer.sink.valid, 1),
                    NextValue(buffer.sink.be, (0b1111 << i) & 0b1111),
                    NextValue(buffer.sink.data[i*8:(i+1)*8], C(0x55, 8)), # First Preamble Byte.
                    NextState("DATA")
                )
            ) for i in range(4)),
            # check for Idle word
            *(If(char_is_k[i],
                If(match(i, K(28, 5)),
                    If(match(i+1, D( 5, 6)) | # /I1/.
                       match(i+1, D( 16, 2)), # /I2/.
                        self.seen_valid_ci.eq(1),
                    )
                ),
            ) for i in range(3))# don't check IDLE word if it requiere another state on the FSM, routing is already too tight
        )
        fsm.act("DATA",
            self.seen_valid_ci.eq(1),
            NextValue(buffer.sink.data, data),
            If((char_is_k == 0),
                # Receive Data.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b1111),
            ).Elif((char_is_k[0] == 1) & match(0, K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 0),
                NextState("START"),
            ).Elif((char_is_k[:2] == 0b0010) & match(1, K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b0001),
                NextState("START"),
            ).Elif((char_is_k[:3] == 0b0100) & match(2, K(29, 7)),
                # K-character is End-of-packet /S/.
                NextValue(buffer.sink.valid, 1),
                NextValue(buffer.sink.be, 0b0011),
                NextState("START"),
            ).Elif((char_is_k == 0b1000) & match(3, K(29, 7)),
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

        rem = Signal(32, reset_less=True)
        rem_be = Signal(4, reset_less=True)

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
                    NextValue(self.source.data, rem),
                    NextValue(self.source.last, 1),
                    NextState('IDLE'),
                )
