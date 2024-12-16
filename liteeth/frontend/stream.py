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
            level   = Signal(max=fifo_depth+1)
            counter = Signal(max=fifo_depth+1)

            _ip_address = Signal(32)
            _udp_port   = Signal(16)

            self.fifo = fifo = stream.SyncFIFO([("data", data_width)], fifo_depth, buffered=True)
            self.comb += sink.connect(fifo.sink)

            self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
            self.comb += fsm.reset.eq(~self.enable)

            fsm.act("IDLE",
                NextValue(counter, 0),
                NextValue(_ip_address, self.ip_address),
                NextValue(_udp_port, self.udp_port),
                # Send FIFO contents when:
                # - We have a full packet:
                If(fifo.sink.valid & fifo.sink.ready & fifo.sink.last,
                    NextValue(level, fifo.level + 1), # +1 for level latency.
                    NextState("SEND")
                ),
                # - Or when FIFO is full.
                If(~fifo.sink.ready,
                    NextValue(level, fifo_depth),
                    NextState("SEND")
                ),
            )
            fsm.act("SEND",
                source.valid.eq(1),
                source.last.eq(counter == (level - 1)),
                source.src_port.eq(_udp_port),
                source.dst_port.eq(_udp_port),
                source.ip_address.eq(_ip_address),
                source.length.eq(level * (data_width//8)),
                source.data.eq(fifo.source.data),
                If(source.last,
                    source.last_be.eq({
                        64 : 0b10000000,
                        32 : 0b1000,
                        16 : 0b10,
                        8  : 0b1
                    }[data_width])),
                If(source.ready,
                    fifo.source.ready.eq(1),
                    NextValue(counter, counter + 1),
                    If(source.last,
                        NextState("IDLE")
                    )
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
