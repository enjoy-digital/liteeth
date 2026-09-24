#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *
from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *

# Helpers ------------------------------------------------------------------------------------------

def tkeep2last_be(keep):
    """AXI-Stream tkeep (contiguous mask of the valid bytes) -> LiteEth last_be (one-hot on the
    last valid byte). A tkeep of 0 gives a last_be of 0 (full word for the streamer)."""
    return keep & ~(keep >> 1)

def last_be2tkeep(last_be, last, keep_width):
    """LiteEth last_be (one-hot on the last valid byte) -> AXI-Stream tkeep (contiguous mask of the
    valid bytes). Full mask when not last or when last_be is 0 (full word)."""
    return Mux(last & (last_be != 0), (last_be << 1) - 1, 2**keep_width - 1)

def _ip_address_udp_port_signals(module, ip_address, udp_port, with_csr):
    """IP address / UDP port as constants (reset values, CSR-overridable) or as dynamic Signals
    (e.g. pads of a standalone core), which must not be used as reset values."""
    if isinstance(ip_address, Signal):
        assert not with_csr
        ip_address_sig = Signal(32)
        module.comb += ip_address_sig.eq(ip_address)
    else:
        ip_address_sig = Signal(32, reset=convert_ip(ip_address))
    if isinstance(udp_port, Signal):
        assert not with_csr
        udp_port_sig = Signal(16)
        module.comb += udp_port_sig.eq(udp_port)
    else:
        udp_port_sig = Signal(16, reset=udp_port)
    return ip_address_sig, udp_port_sig

# Stream to UDP TX ---------------------------------------------------------------------------------

class LiteEthStream2UDPTX(LiteXModule):
    """Stream to UDP TX.

    Packetizes a data stream into UDP packets:
    - Without FIFO (``fifo_depth=None``): each word is sent as a UDP packet.
    - With FIFO: packets are delimited by ``sink.last`` (or by a full FIFO, in which case the packet
      is split).

    When ``with_last_be`` is set, ``sink`` carries a ``last_be`` byte-enable (one-hot on the last
    valid byte of the last word) allowing byte-granular packet lengths. A ``last_be`` of 0 on the
    last word is interpreted as a full word (legacy behaviour).
    """
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_csr=False,
        with_last_be = False,
    ):
        sink_description = eth_tty_tx_description(data_width, with_last_be=with_last_be)
        self.sink   = sink   = stream.Endpoint(sink_description)
        self.source = source = stream.Endpoint(eth_udp_user_description(data_width))

        # # #

        bytes_per_word = data_width//8
        full_last_be   = 1 << (bytes_per_word - 1)

        self.ip_address, self.udp_port = _ip_address_udp_port_signals(self,
            ip_address = ip_address,
            udp_port   = udp_port,
            with_csr   = with_csr,
        )
        self.enable = Signal(reset=1)

        if with_csr:
            self.add_csr()

        # Last Byte-Enable decoding: normalized one-hot last_be and number of valid bytes in the
        # last word (a last_be of 0 is interpreted as a full word).
        sink_last_be    = Signal(bytes_per_word)
        sink_last_bytes = Signal(max=bytes_per_word + 1)
        if with_last_be:
            last_be_cases = {}
            for i in range(bytes_per_word):
                last_be_cases[1 << i] = [
                    sink_last_be.eq(1 << i),
                    sink_last_bytes.eq(i + 1),
                ]
            last_be_cases["default"] = [
                sink_last_be.eq(full_last_be),
                sink_last_bytes.eq(bytes_per_word),
            ]
            self.comb += Case(sink.last_be, last_be_cases)
        else:
            self.comb += [
                sink_last_be.eq(full_last_be),
                sink_last_bytes.eq(bytes_per_word),
            ]

        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"valid", "ready", "data"}),
                source.last.eq(1),
                source.last_be.eq(sink_last_be),
                source.src_port.eq(self.udp_port),
                source.dst_port.eq(self.udp_port),
                source.ip_address.eq(self.ip_address),
                source.length.eq(sink_last_bytes),
            ]
        else:
            counter = Signal(max=fifo_depth+1)

            _ip_address = Signal(32)
            _udp_port   = Signal(16)

            packet_last    = Signal()
            packet_full    = Signal()
            packet_length  = Signal(16)
            packet_last_be = Signal(bytes_per_word)
            source_active  = Signal()

            fifo_payload_layout = [("data", data_width)]
            if with_last_be:
                fifo_payload_layout += [("last_be", bytes_per_word)]
            self.fifo = fifo = PacketFIFO(
                layout         = stream.EndpointDescription(
                    payload_layout = fifo_payload_layout,
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
                # Last word of the packet: sink's last_be on sink.last, full word when split on a
                # full FIFO.
                If(sink.last,
                    packet_last_be.eq(sink_last_be),
                    packet_length.eq(counter*bytes_per_word + sink_last_bytes),
                ).Else(
                    packet_last_be.eq(full_last_be),
                    packet_length.eq((counter + 1)*bytes_per_word),
                ),

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
                source.src_port.eq(Mux(source_active, _udp_port, self.udp_port)),
                source.dst_port.eq(Mux(source_active, _udp_port, self.udp_port)),
                source.ip_address.eq(Mux(source_active, _ip_address, self.ip_address)),
                source.length.eq(fifo.source.length),
            ]
            if with_last_be:
                self.comb += [
                    fifo.sink.last_be.eq(Mux(packet_last, packet_last_be, 0)),
                    source.last_be.eq(fifo.source.last_be),
                ]
            else:
                self.comb += If(source.last, source.last_be.eq(full_last_be))

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
    """UDP to Stream RX.

    Filters incoming UDP packets on ``udp_port`` (and optionally ``ip_address``) and outputs their
    payload as a data stream. When ``with_last_be`` is set, ``source`` carries the ``last_be``
    byte-enable (one-hot on the last valid byte of the last word) so byte-granular packet lengths
    are preserved.
    """
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_broadcast=True,
        with_csr     = False,
        with_last_be = False,
    ):
        source_description = eth_tty_rx_description(data_width, with_last_be=with_last_be)
        self.sink   = sink   = stream.Endpoint(eth_udp_user_description(data_width))
        self.source = source = stream.Endpoint(source_description)

        # # #

        self.ip_address, self.udp_port = _ip_address_udp_port_signals(self,
            ip_address = ip_address,
            udp_port   = udp_port,
            with_csr   = with_csr,
        )
        self.enable = Signal(reset=1)

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
        keep = {"last", "data", "error"}
        if with_last_be:
            keep |= {"last_be"}
        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep=keep),
                source.valid.eq(sink.valid & valid),
                sink.ready.eq(source.ready | ~valid)
            ]
        else:
            fifo_layout = [("data", data_width), ("error", 1)]
            if with_last_be:
                fifo_layout += [("last_be", data_width//8)]
            self.fifo = fifo = stream.SyncFIFO(
                layout   = fifo_layout,
                depth    = fifo_depth,
                buffered = True,
            )
            self.comb += [
                sink.connect(fifo.sink, keep=keep),
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
    def __init__(self, udp, ip_address, udp_port, data_width=8, rx_fifo_depth=64, tx_fifo_depth=64,
        with_broadcast = True,
        cd             = "sys",
        with_last_be   = False,
    ):
        self.tx = tx = LiteEthStream2UDPTX(ip_address, udp_port, data_width, tx_fifo_depth,
            with_last_be = with_last_be,
        )
        self.rx = rx = LiteEthUDP2StreamRX(ip_address, udp_port, data_width, rx_fifo_depth,
            with_broadcast = with_broadcast,
            with_last_be   = with_last_be,
        )
        udp_port = udp.crossbar.get_port(udp_port, dw=data_width, cd=cd)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
