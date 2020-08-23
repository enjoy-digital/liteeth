#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

from migen.genlib.misc import chooser

# MAC Preamble Inserter ----------------------------------------------------------------------------

class LiteEthMACPreambleInserter(Module):
    """Preamble inserter

    Inserts preamble at the beginning of each packet.

    Attributes
    ----------
    sink : in
        Packet octets.
    source : out
        Preamble, SFD, and packet octets.
    """
    def __init__(self, dw):
        self.sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # # #

        preamble = Signal(64, reset=eth_preamble)
        count    = Signal(max=(64//dw)-1, reset_less=True)
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self.sink.ready.eq(1),
            NextValue(count, 0),
            If(self.sink.valid,
                self.sink.ready.eq(0),
                NextState("PREAMBLE"),
            )
        )
        fsm.act("PREAMBLE",
            self.source.valid.eq(1),
            chooser(preamble, count, self.source.data),
            If(self.source.ready,
                If(count == (64//dw)-1,
                    NextState("COPY")
                ).Else(
                    NextValue(count, count + 1)
                )
            )
        )
        self.comb += [
            self.source.data.eq(self.sink.data),
            self.source.last_be.eq(self.sink.last_be)
        ]
        fsm.act("COPY",
            self.sink.connect(self.source, omit={"data", "last_be"}),

            If(self.sink.valid & self.sink.last & self.source.ready,
                NextState("IDLE"),
            )
        )

# MAC Preamble Checker ----------------------------------------------------------------------------

class LiteEthMACPreambleChecker(Module):
    """Preamble checker

    Detects preamble at the beginning of each packet.

    Attributes
    ----------
    sink : in
        Bits input.
    source : out
        Packet octets starting immediately after SFD.
    error : out
        Pulses every time a preamble error is detected.
    """
    def __init__(self, dw):
        assert dw == 8
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        self.error = Signal()

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="PREAMBLE")
        fsm.act("PREAMBLE",
            sink.ready.eq(1),
            If(sink.valid & ~sink.last & (sink.data == (eth_preamble >> 56)),
                NextState("COPY")
            ),
            If(sink.valid & sink.last, self.error.eq(1))
        )
        self.comb += [
            source.data.eq(sink.data),
            source.last_be.eq(sink.last_be)
        ]
        fsm.act("COPY",
            sink.connect(source, omit={"data", "last_be"}),
            If(source.valid & source.last & source.ready,
                NextState("PREAMBLE"),
            )
        )
