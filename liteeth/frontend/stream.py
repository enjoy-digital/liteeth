#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

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
            self.packet_len = packet_len = Signal(max=fifo_depth+1)
            self.counter = counter = Signal(max=fifo_depth+1)

            _ip_address = Signal(32)
            _udp_port   = Signal(16)

            self.fifo = fifo = stream.SyncFIFO([("data", data_width)], fifo_depth, buffered=True)

            self.fsm = fsm = ResetInserter()(FSM(reset_state="STORE"))
            self.comb += fsm.reset.eq(~self.enable)

            fsm.act("STORE",
                sink.connect(fifo.sink),
                NextValue(counter, 0),
                NextValue(_ip_address, self.ip_address),
                NextValue(_udp_port, self.udp_port),

                # Send FIFO contents when:
                If(fifo.sink.valid & fifo.sink.ready,
                    NextValue(packet_len, packet_len + 1),
                    # - We have a full packet:
                    If(fifo.sink.last,
                        NextState("SEND"),
                    ),
                ),
                # - Or when FIFO is full.
                If(~fifo.sink.ready,
                    NextValue(packet_len, fifo_depth),
                    NextState("SEND")
                ),
            )
            fsm.act("SEND",
                fifo.source.connect(source, keep=["data", "valid", "ready"]),
                source.last.eq(fifo.source.last),
                source.src_port.eq(_udp_port),
                source.dst_port.eq(_udp_port),
                source.ip_address.eq(_ip_address),
                source.length[{8: 0, 16: 1, 32: 2, 64: 3}[data_width]:].eq(packet_len),
                If(source.last,
                    source.last_be.eq({
                        64 : 0b10000000,
                        32 : 0b1000,
                        16 : 0b10,
                        8  : 0b1
                    }[data_width])),
                If(source.ready & source.valid,
                    NextValue(counter, counter + 1),
                    If(fifo.source.last,
                        NextValue(packet_len, 0),
                        NextState("STORE"),
                    ),
                )
            )

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
