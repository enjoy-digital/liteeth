#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

# Stream to UDP TX -----------------------------------------------------------------------------------

class LiteEthStream2UDPTX(Module):
    def __init__(self, ip_address, udp_port, data_width=8, fifo_depth=None):
        self.sink   = sink   = stream.Endpoint(eth_tty_tx_description(data_width))
        self.source = source = stream.Endpoint(eth_udp_user_description(data_width))

        # # #

        ip_address = convert_ip(ip_address)

        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"valid", "ready", "data"}),
                source.last.eq(1),
                source.src_port.eq(udp_port),
                source.dst_port.eq(udp_port),
                source.ip_address.eq(ip_address),
                source.length.eq(data_width//8)
            ]
        else:
            level   = Signal(max=fifo_depth+1)
            counter = Signal(max=fifo_depth+1, reset=1)

            # sink FIFO
            self.submodules.fifo = fifo = stream.SyncFIFO([("data", data_width)], fifo_depth, buffered=True)

            # avoid assering FIFO we when the FIFO is full
            fifo_almost_full = Signal()
            self.comb += fifo_almost_full.eq(fifo.level >= fifo_depth - 1)

            # FSM
            self.submodules.fsm = fsm = FSM(reset_state="READ")
            fsm.act("READ",
                If(sink.valid,
                    sink.ready.eq(1),
                    fifo.sink.data.eq(sink.data),
                    fifo.sink.valid.eq(1), # _we
                    fifo.sink.last.eq(fifo_almost_full | sink.last),
                    NextValue(counter, counter + 1),
                    If(fifo.sink.last,
                       NextValue(level, counter),
                       NextValue(counter, 1),
                       NextState("WRITE")
                    )
                )
            )
            fsm.act("WRITE",
                source.valid.eq(1),
                source.last.eq(counter == level),
                source.src_port.eq(udp_port),
                source.dst_port.eq(udp_port),
                source.ip_address.eq(ip_address),
                source.length.eq(level * (data_width//8)),
                source.data.eq(fifo.source.data),
                source.last_be.eq(2**(data_width//8 - 1)),
                If(source.ready & fifo.source.valid,
                    fifo.source.ready.eq(1),
                    NextValue(counter, counter + 1),
                    If(source.last,
                       NextValue(counter, 1),
                       NextState("READ")
                    )
                )
            )


# UDP to Stream RX ---------------------------------------------------------------------------------

class LiteEthUDP2StreamRX(Module):
    def __init__(self, ip_address=None, udp_port=None, data_width=8, fifo_depth=None, with_broadcast=True):
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(data_width))
        self.source = source = stream.Endpoint(eth_tty_rx_description(data_width))

        # # #

        valid = Signal(reset=1)

        # Check UDP Port.
        assert udp_port is not None
        self.comb += If(sink.dst_port != udp_port, valid.eq(0))

        # Check IP Address (Optional).
        if (ip_address is not None) and (not with_broadcast):
            ip_address = convert_ip(ip_address)
            self.comb += If(sink.ip_address != ip_address, valid.eq(0))

        # Data-Path / Buffering (Optional).
        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"last", "data", "error"}),
                source.valid.eq(sink.valid & valid),
                sink.ready.eq(source.ready | ~valid)
            ]
        else:
            self.submodules.fifo = fifo = stream.SyncFIFO(
                layout   = [("data", data_width), ("error", 1)],
                depth    = fifo_depth,
                buffered = True,
            )
            self.comb += [
                sink.connect(fifo.sink, keep={"last", "data", "error"}),
                fifo.sink.valid.eq(sink.valid & valid),
                sink.ready.eq(fifo.sink.ready | ~valid),
                fifo.source.connect(source)
            ]

# UDP Streamer -------------------------------------------------------------------------------------

class LiteEthUDPStreamer(Module):
    def __init__(self, udp, ip_address, udp_port, data_width=8, rx_fifo_depth=64, tx_fifo_depth=64, with_broadcast=True, cd="sys"):
        self.submodules.tx = tx = LiteEthStream2UDPTX(ip_address, udp_port, data_width, tx_fifo_depth)
        self.submodules.rx = rx = LiteEthUDP2StreamRX(ip_address, udp_port, data_width, rx_fifo_depth, with_broadcast)
        udp_port = udp.crossbar.get_port(udp_port, dw=data_width, cd=cd)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
