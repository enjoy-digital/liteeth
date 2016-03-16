from liteeth.common import *

from litex.gen.genlib.misc import chooser


class LiteEthMACPreambleInserter(Module):
    def __init__(self, dw):
        self.sink = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # # #

        preamble = Signal(64, reset=eth_preamble)
        cnt_max = (64//dw)-1
        cnt = Signal(max=cnt_max+1)
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
            self.sink.connect(self.source, leave_out=set(["data", "last_be"])),

            If(self.sink.valid & self.sink.last & self.source.ready,
                NextState("IDLE"),
            )
        )


class LiteEthMACPreambleChecker(Module):
    def __init__(self, dw):
        self.sink = stream.Endpoint(eth_phy_description(dw))
        self.source = stream.Endpoint(eth_phy_description(dw))

        # # #

        preamble = Signal(64, reset=eth_preamble)
        cnt_max = (64//dw) - 1
        cnt = Signal(max=cnt_max+1)
        clr_cnt = Signal()
        inc_cnt = Signal()

        self.sync += \
            If(clr_cnt,
                cnt.eq(0)
            ).Elif(inc_cnt,
                cnt.eq(cnt+1)
            )

        discard = Signal()
        clr_discard = Signal()
        set_discard = Signal()

        self.sync += \
            If(clr_discard,
                discard.eq(0)
            ).Elif(set_discard,
                discard.eq(1)
            )

        ref = Signal(dw)
        match = Signal()
        self.comb += [
            chooser(preamble, cnt, ref),
            match.eq(self.sink.data == ref)
        ]

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        fsm.act("IDLE",
            self.sink.ready.eq(1),
            clr_cnt.eq(1),
            clr_discard.eq(1),
            If(self.sink.valid,
                clr_cnt.eq(0),
                inc_cnt.eq(1),
                clr_discard.eq(0),
                set_discard.eq(~match),
                NextState("CHECK"),
            )
        )
        fsm.act("CHECK",
            self.sink.ready.eq(1),
            If(self.sink.valid,
                set_discard.eq(~match),
                If(cnt == cnt_max,
                    If(discard | (~match),
                        NextState("IDLE")
                    ).Else(
                        NextState("COPY")
                    )
                ).Else(
                    inc_cnt.eq(1)
                )
            )
        )
        self.comb += [
            self.source.data.eq(self.sink.data),
            self.source.last_be.eq(self.sink.last_be)
        ]
        fsm.act("COPY",
            self.sink.connect(self.source, leave_out=set(["data", "last_be"])),
            If(self.source.valid & self.source.last & self.source.ready,
                NextState("IDLE"),
            )
        )
