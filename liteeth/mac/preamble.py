# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# This file is Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# License: BSD

from liteeth.common import *

from migen.genlib.misc import chooser


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
        self.sink = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # # #

        preamble = Signal(64, reset=eth_preamble)
        cnt_max = (64//dw)-1
        cnt = Signal(max=cnt_max+1, reset_less=True)
        clr_cnt = Signal()
        inc_cnt = Signal()

        self.sync += \
            If(clr_cnt,
                cnt.eq(0)
            ).Elif(inc_cnt,
                cnt.eq(cnt+1)
            )

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm
        fsm.act("IDLE",
            self.sink.ready.eq(1),
            clr_cnt.eq(1),
            If(self.sink.valid,
                self.sink.ready.eq(0),
                NextState("INSERT"),
            )
        )
        fsm.act("INSERT",
            self.source.valid.eq(1),
            chooser(preamble, cnt, self.source.data),
            If(cnt == cnt_max,
                If(self.source.ready, NextState("COPY"))
            ).Else(
                inc_cnt.eq(self.source.ready)
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


class LiteEthMACPreambleChecker(Module):
    """Preamble detector

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
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        self.error = Signal()

        # # #

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid & ~sink.last & (sink.data == eth_preamble >> 56),
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
                NextState("IDLE"),
            )
        )
