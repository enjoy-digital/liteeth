#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

# Steam 2 UDP TX -----------------------------------------------------------------------------------

class LiteEthStream2UDPTX(Module):
    def __init__(self, ip_address, udp_port, fifo_depth=None, send_level=1):
        self.sink   = sink   = stream.Endpoint(eth_tty_description(8))
        self.source = source = stream.Endpoint(eth_udp_user_description(8))

        # # #

        ip_address = convert_ip(ip_address)

        if fifo_depth is None:
            assert send_level == 1
            self.comb += [
                sink.connect(source, keep={"valid", "ready", "data"}),
                source.last.eq(1),
                source.src_port.eq(udp_port),
                source.dst_port.eq(udp_port),
                source.ip_address.eq(ip_address),
                source.length.eq(1)
            ]
        else:
            level   = Signal(max=fifo_depth+1)
            counter = Signal(max=fifo_depth+1)

            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += sink.connect(fifo.sink)

            self.submodules.fsm = fsm = FSM(reset_state="IDLE")
            fsm.act("IDLE",
                If(fifo.level >= send_level,
                    NextValue(level, fifo.level),
                    NextValue(counter, 0),
                    NextState("SEND")
                )
            )
            fsm.act("SEND",
                source.valid.eq(1),
                source.last.eq(counter == (level - 1)),
                source.src_port.eq(udp_port),
                source.dst_port.eq(udp_port),
                source.ip_address.eq(ip_address),
                source.length.eq(level),
                source.data.eq(fifo.source.data),
                If(source.ready,
                    fifo.source.ready.eq(1),
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

        ip_address = convert_ip(ip_address)

        valid = Signal()
        self.comb += valid.eq(
            (sink.ip_address == ip_address) &
            (sink.dst_port   == udp_port)
        )
        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"last", "ready", "data"}),
                source.valid.eq(sink.valid & valid),
            ]
        else:
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", 8)], fifo_depth)
            self.comb += [
                sink.connect(fifo.sink, keep={"last", "ready", "data"}),
                fifo.sink.valid.eq(sink.valid & valid),
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
