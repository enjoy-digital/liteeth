#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *
from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *

# Stream to UDP TX ---------------------------------------------------------------------------------

class LiteEthStream2UDPTX(LiteXModule):
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_csr=False):
        self.sink   = sink   = stream.Endpoint(eth_tty_tx_description(data_width))
        self.source = source = stream.Endpoint(eth_udp_user_description(data_width))

        # # #

        self.ip_address = Signal(32, reset=convert_ip(ip_address))
        self.udp_port   = Signal(16, reset=udp_port)
        self.enable     = Signal(reset=1)

        if with_csr:
            self.add_csr()

        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"valid", "ready", "data"}),
                source.last.eq(1),
                source.src_port.eq(self.udp_port),
                source.dst_port.eq(self.udp_port),
                source.ip_address.eq(self.ip_address),
                source.length.eq(data_width // 8)
            ]
        else:
            counter = Signal(max=fifo_depth+1)

            _ip_address = Signal(32)
            _udp_port   = Signal(16)

            packet_last   = Signal()
            packet_full   = Signal()
            packet_length = Signal(16)
            source_active = Signal()

            self.fifo = fifo = PacketFIFO(
                layout         = stream.EndpointDescription(
                    payload_layout = [("data", data_width)],
                    param_layout   = [("length", 16)],
                ),
                payload_depth = fifo_depth,
                param_depth   = fifo_depth,
                buffered      = True,
            )

            if fifo_depth > 0:
                self.comb += packet_full.eq(counter == (fifo_depth - 1))

            self.comb += [
                packet_last.eq(sink.last | packet_full),
                packet_length.eq((counter + 1) * (data_width//8)),

                # Input.
                sink.ready.eq(fifo.sink.ready),
                fifo.sink.valid.eq(sink.valid),
                fifo.sink.last.eq(packet_last),
                fifo.sink.data.eq(sink.data),
                fifo.sink.length.eq(packet_length),

                # Output.
                source.valid.eq(fifo.source.valid & (self.enable | source_active)),
                fifo.source.ready.eq(source.ready & (self.enable | source_active)),
                source.data.eq(fifo.source.data),
                source.last.eq(fifo.source.last),
                If(source.last,
                    source.last_be.eq({
                        64 : 0b10000000,
                        32 : 0b1000,
                        16 : 0b10,
                        8  : 0b1
                    }[data_width])),
                source.src_port.eq(Mux(source_active, _udp_port, self.udp_port)),
                source.dst_port.eq(Mux(source_active, _udp_port, self.udp_port)),
                source.ip_address.eq(Mux(source_active, _ip_address, self.ip_address)),
                source.length.eq(fifo.source.length),
            ]

            self.sync += [
                If(sink.valid & sink.ready,
                    If(packet_last,
                        counter.eq(0)
                    ).Else(
                        counter.eq(counter + 1)
                    )
                ),
                If(fifo.source.valid & self.enable & ~source_active,
                    source_active.eq(1),
                    _ip_address.eq(self.ip_address),
                    _udp_port.eq(self.udp_port),
                ),
                If(source.valid & source.ready & source.last,
                    source_active.eq(0)
                )
            ]

    def add_csr(self):
        self._enable     = CSRStorage(1, description="Enable Module", reset=1)
        self._ip_address = CSRStorage(32, description="IP Address", reset=self.ip_address.reset.value)
        self._udp_port   = CSRStorage(16, description="UDP Port",   reset=self.udp_port.reset.value)

        # # #

        self.comb += [
            self.enable.eq(self._enable.storage),
            self.ip_address.eq(self._ip_address.storage),
            self.udp_port.eq(self._udp_port.storage),
        ]


# UDP to Stream RX ---------------------------------------------------------------------------------

class LiteEthUDP2StreamRX(LiteXModule):
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_broadcast=True, with_csr=False):
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(data_width))
        self.source = source = stream.Endpoint(eth_tty_rx_description(data_width))

        # # #

        self.ip_address = Signal(32, reset=convert_ip(ip_address))
        self.udp_port   = Signal(16, reset=udp_port)
        self.enable     = Signal(reset=1)

        if with_csr:
            self.add_csr()

        valid = Signal(reset=1)

        # Disable RX when enable=0.
        self.comb += If(~self.enable, valid.eq(0))

        # Check UDP Port.
        self.comb += If(sink.dst_port != self.udp_port, valid.eq(0))

        # Check IP Address (Optional).
        if not with_broadcast:
            self.comb += If(sink.ip_address != self.ip_address, valid.eq(0))

        # Data-Path / Buffering (Optional).
        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"last", "data", "error"}),
                source.valid.eq(sink.valid & valid),
                sink.ready.eq(source.ready | ~valid)
            ]
        else:
            self.fifo = fifo = stream.SyncFIFO(
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

    def add_csr(self):
        self._enable     = CSRStorage(1,  description="Enable Module", reset=1)
        self._ip_address = CSRStorage(32, description="IP Address",    reset=self.ip_address.reset.value)
        self._udp_port   = CSRStorage(16, description="UDP Port",      reset=self.udp_port.reset.value)

        self.comb += [
            self.enable.eq(self._enable.storage),
            self.ip_address.eq(self._ip_address.storage),
            self.udp_port.eq(self._udp_port.storage),
        ]


# UDP Streamer -------------------------------------------------------------------------------------

class LiteEthUDPStreamer(LiteXModule):
    def __init__(self, udp, ip_address, udp_port, data_width=8, rx_fifo_depth=64, tx_fifo_depth=64, with_broadcast=True, cd="sys"):
        self.tx = tx = LiteEthStream2UDPTX(ip_address, udp_port, data_width, tx_fifo_depth)
        self.rx = rx = LiteEthUDP2StreamRX(ip_address, udp_port, data_width, rx_fifo_depth, with_broadcast)
        udp_port = udp.crossbar.get_port(udp_port, dw=data_width, cd=cd)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
