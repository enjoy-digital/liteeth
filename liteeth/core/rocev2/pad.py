from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import Endpoint, Buffer

# IBT Padding --------------------------------------------------------------------------------------

class LiteEthIBTPaddingInserter(LiteXModule):
    def __init__(self, dw, with_buffer=True):
        self.sink   = sink   = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1)]))
        self.source = source = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1)]))

        # # #

        assert dw in [8]

        # Connect all parameter signals
        self.comb += sink.connect(source, omit={"valid", "ready", "last", "data"})

        counter = Signal(2)

        # FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(~sink.header_only,
                sink.connect(source, keep={"valid", "data"}),
                source.last.eq(sink.last & (counter == 3)),
                sink.ready.eq((~sink.last | (counter == 3)) & source.ready),
                If(sink.valid & source.ready,
                    NextValue(counter, counter + 1),
                    If(sink.last & (counter != 3),
                        NextState("PADDING")
                    )
                )
            ).Else(
                sink.connect(source, keep={"valid", "data", "ready", "last"})
            ),
        )
        fsm.act("PADDING",
            source.valid.eq(1),
            # Pad with 0s
            source.data.eq(0),
            source.last.eq(counter == 3),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(source.last,
                    sink.ready.eq(1),
                    NextState("IDLE")
                )
            )
        )

# Behaviour when a zero-length packet claims to have padding or when a packet is
# not a multiple of 4 is not specified. In our case, we send a Invalid Request NAK
class LiteEthIBTPaddingRemover(LiteXModule):
    def __init__(self, dw):
        self.sink   = sink   = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1)]))
        self.source = source = Endpoint(add_params(eth_rocev2_description(dw), [("error", 1), ("header_only", 1)]))

        # # #

        # Connect all parameter signals
        self.comb += sink.connect(source, omit={"valid", "ready", "last", "data"})

        # Signals
        # We add a 4 byte latency to pipeline to detect last 4 bytes and treat them accordingly
        pipe       = Signal(32, reset_less=True)
        shift_pipe = Signal()
        fill_cnt   = Signal(2)
        dat_cnt    = Signal(2, reset_less=True)

        self.sync += [
            If(shift_pipe, pipe.eq(Cat(pipe[8:], sink.data)))
        ]

        # FSM
        self.fsm = fsm = FSM(reset_state="FILL")
        fsm.act("FILL",
            If(sink.header_only,
                sink.connect(source, keep={"valid", "ready", "last", "data"}),
                If(sink.pad != 0,
                    NextState("ERROR")
                )
            ).Else(
                shift_pipe.eq(sink.valid & source.ready),
                sink.ready.eq(1),
                If(sink.valid,
                    If(source.ready,
                        NextValue(fill_cnt, fill_cnt + 1),
                        If(fill_cnt == 3,
                            NextState("PASSTHROUGH"),
                            If(sink.last,
                                NextValue(dat_cnt, 3 - sink.pad),
                                NextState("PAD")
                            )
                        )
                    ),
                    If((fill_cnt != 3) & sink.last,
                        source.valid.eq(1),
                        source.last.eq(1),
                        If(source.ready,
                            NextState("ERROR"),
                            NextValue(fill_cnt, 0)
                        )
                    )
                ),
            )
        )

        fsm.act("PASSTHROUGH",
            source.valid.eq(sink.valid),
            sink.ready.eq(source.ready),
            source.data.eq(pipe[:8]),
            shift_pipe.eq(sink.valid & source.ready),
            If(sink.valid & source.ready,
                NextValue(fill_cnt, fill_cnt + 1),
            ),
            If(sink.valid & sink.last,
                If(source.ready & (fill_cnt == 3),
                    sink.ready.eq(0),
                    NextValue(dat_cnt, 3 - sink.pad),
                    NextState("PAD")
                ),
                If(fill_cnt != 3,
                    source.last.eq(1),
                    If(source.ready,
                        NextState("ERROR"),
                        NextValue(fill_cnt, 0)
                    )
                )
            )
        )

        fsm.act("PAD",
            source.valid.eq(1),
            source.data.eq(pipe[:8]),
            shift_pipe.eq(source.ready),
            NextValue(dat_cnt, dat_cnt - source.ready),
            source.last.eq(dat_cnt == 0),
            If(source.ready & source.last,
                sink.ready.eq(1),
                NextState("FILL")
            )
        )

        fsm.act("ERROR",
            source.error.eq(1),
            NextState("FILL")
        )
