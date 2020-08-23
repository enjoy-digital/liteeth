#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

# Steam 2 UDP TX -----------------------------------------------------------------------------------

class LiteEthStream2UDPTX(Module):
    def __init__(self, ip_address, udp_port, fifo_depth=None):
        self.sink   = sink   = stream.Endpoint(eth_tty_description(8))
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # # #

        if fifo_depth is None:
            self.comb += [
                source.valid.eq(sink.valid),
                source.last.eq(1),
                source.length.eq(1),
                source.data.eq(sink.data),
                sink.ready.eq(source.ready)
            ]
        else:
            level   = Signal(max=fifo_depth)
            counter = Signal(max=fifo_depth)

            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += sink.connect(fifo.sink)

            self.submodules.fsm = fsm = FSM(reset_state="IDLE")
            fsm.act("IDLE",
                If(fifo.source.valid,
                    NextValue(level, fifo.level),
                    NextValue(counter, 0),
                    NextState("SEND")
                )
            )
            fsm.act("SEND",
                source.valid.eq(fifo.source.valid),
                If(level == 0,
                    source.last.eq(1),
                ).Else(
                    source.last.eq(counter == (level-1)),
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
                fifo.source.ready.eq(source.ready),
                If(source.valid & source.ready,
                    NextValue(counter, counter + 1),
                    If(source.last,
                        NextState("IDLE")
                    )
                )
            )

# UDP to Stream RX ---------------------------------------------------------------------------------

class LiteEthUDP2StreamRX(Module):
    def __init__(self, ip_address, udp_port, fifo_depth=None):
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(8))
        self.source = source = stream.Endpoint(eth_tty_description(8))

        # # #

        valid = Signal()
        self.comb += valid.eq(
            (sink.ip_address == ip_address) &
            (sink.dst_port == udp_port)
        )
        if fifo_depth is None:
            self.comb += [
                source.valid.eq(sink.valid & valid),
                source.data.eq(sink.data),
                sink.ready.eq(source.ready)
            ]
        else:
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += [
                fifo.sink.valid.eq(sink.valid & valid),
                fifo.sink.data.eq(sink.data),
                sink.ready.eq(fifo.sink.ready),
                fifo.source.connect(source)
            ]

# UDP Streamer -------------------------------------------------------------------------------------

class LiteEthUDPStreamer(Module):
    def __init__(self, udp, ip_address, udp_port, rx_fifo_depth=64, tx_fifo_depth=64):
        self.submodules.tx = tx = LiteEthStream2UDPTX(ip_address, udp_port, tx_fifo_depth)
        self.submodules.rx = rx = LiteEthUDP2StreamRX(ip_address, udp_port, rx_fifo_depth)
        udp_port = udp.crossbar.get_port(udp_port, dw=8)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
