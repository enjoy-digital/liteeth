from liteeth.common import *


class LiteEthTTYTX(Module):
    def __init__(self, ip_address, udp_port, fifo_depth=None):
        self.sink = sink = stream.Endpoint(eth_tty_description(8))
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # # #

        if fifo_depth is None:
            self.comb += [
                source.stb.eq(sink.stb),
                source.sop.eq(1),
                source.eop.eq(1),
                source.length.eq(1),
                source.data.eq(sink.data),
                sink.ack.eq(source.ack)
            ]
        else:
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += sink.connect(fifo.sink)

            level = Signal(max=fifo_depth)
            level_update = Signal()
            self.sync += If(level_update, level.eq(fifo.level))

            counter = Signal(max=fifo_depth)
            counter_reset = Signal()
            counter_ce = Signal()
            self.sync += \
                If(counter_reset,
                    counter.eq(0)
                ).Elif(counter_ce,
                    counter.eq(counter + 1)
                )

            self.submodules.fsm = fsm = FSM(reset_state="IDLE")
            fsm.act("IDLE",
                If(fifo.source.stb,
                    level_update.eq(1),
                    counter_reset.eq(1),
                    NextState("SEND")
                )
            )
            fsm.act("SEND",
                source.stb.eq(fifo.source.stb),
                source.sop.eq(counter == 0),
                If(level == 0,
                    source.eop.eq(1),
                ).Else(
                    source.eop.eq(counter == (level-1)),
                ),
                source.src_port.eq(udp_port),
                source.dst_port.eq(udp_port),
                source.ip_address.eq(ip_address),
                If(level == 0,
                    source.length.eq(1),
                ).Else(
                    source.length.eq(level),
                ),
                source.data.eq(fifo.source.data),
                fifo.source.ack.eq(source.ack),
                If(source.stb & source.ack,
                    counter_ce.eq(1),
                    If(source.eop,
                        NextState("IDLE")
                    )
                )
            )


class LiteEthTTYRX(Module):
    def __init__(self, ip_address, udp_port, fifo_depth=None):
        self.sink = sink = stream.Endpoint(eth_udp_user_description(8))
        self.source = source = stream.Endpoint(eth_tty_description(8))

        # # #

        valid = Signal()
        self.comb += valid.eq(
            (sink.ip_address == ip_address) &
            (sink.dst_port == udp_port)
        )
        if fifo_depth is None:
            self.comb += [
                source.stb.eq(sink.stb & valid),
                source.data.eq(sink.data),
                sink.ack.eq(source.ack)
            ]
        else:
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += [
                fifo.sink.stb.eq(sink.stb & valid),
                fifo.sink.data.eq(sink.data),
                sink.ack.eq(fifo.sink.ack),
                fifo.source.connect(source)
            ]


class LiteEthTTY(Module):
    def __init__(self, udp, ip_address, udp_port,
            rx_fifo_depth=64,
            tx_fifo_depth=64):
        self.submodules.tx = tx = LiteEthTTYTX(ip_address, udp_port, tx_fifo_depth)
        self.submodules.rx = rx = LiteEthTTYRX(ip_address, udp_port, rx_fifo_depth)
        udp_port = udp.crossbar.get_port(udp_port, dw=8)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
