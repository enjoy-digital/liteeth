#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *
from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *

# Helpers ------------------------------------------------------------------------------------------

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
    - With FIFO: packets are delimited by ``sink.last`` or split when reaching the maximum packet
      length: ``max_packet_length`` bytes rounded down to full words when set (ex the maximum UDP
      payload for the MTU, 8972 bytes with Jumbo Frames), else the FIFO depth.

    With ``with_be``, ``sink.be`` enables each valid byte. Intermediate words must be full;
    the final word has a nonzero, contiguous mask starting at byte zero.
    """
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_csr=False,
        with_be           = False,
        max_packet_length = None,
    ):
        sink_description = eth_tty_tx_description(data_width, with_be=with_be)
        self.sink   = sink   = stream.Endpoint(sink_description)
        self.source = source = stream.Endpoint(eth_udp_user_description(data_width))

        # # #

        bytes_per_word = data_width//8
        full_be        = (1 << bytes_per_word) - 1

        self.ip_address, self.udp_port = _ip_address_udp_port_signals(self,
            ip_address = ip_address,
            udp_port   = udp_port,
            with_csr   = with_csr,
        )
        self.enable = Signal(reset=1)

        if with_csr:
            self.add_csr()

        sink_be    = Signal(bytes_per_word)
        sink_bytes = Signal(max=bytes_per_word + 1)
        self.comb += [
            sink_be.eq(sink.be if with_be else full_be),
            sink_bytes.eq(stream.byte_count(sink_be)),
        ]

        source_active = Signal()
        _ip_address   = Signal(32)
        _udp_port     = Signal(16)

        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep={"data"}),
                source.valid.eq(sink.valid & (self.enable | source_active)),
                sink.ready.eq(source.ready & (self.enable | source_active)),
                source.last.eq(1),
                source.be.eq(sink_be),
                source.length.eq(sink_bytes),
            ]
        else:
            counter = Signal(max=fifo_depth+1)

            packet_last   = Signal()
            packet_full   = Signal()
            packet_length = Signal(16)

            fifo_payload_layout = [("data", data_width)]
            if with_be:
                fifo_payload_layout += [("be", bytes_per_word)]
            self.fifo = fifo = PacketFIFO(
                layout         = stream.EndpointDescription(
                    payload_layout = fifo_payload_layout,
                    param_layout   = [("length", 16)],
                ),
                payload_depth = fifo_depth,
                param_depth   = fifo_depth,
                buffered      = True,
            )

            # Maximum packet length (in words): packets longer than this are split.
            max_packet_words = fifo_depth
            if max_packet_length is not None:
                max_packet_words = min(fifo_depth, max_packet_length//bytes_per_word)
                assert max_packet_words >= 1, "max_packet_length must be >= data width."
            if max_packet_words > 0:
                self.comb += packet_full.eq(counter == (max_packet_words - 1))

            self.comb += [
                packet_last.eq(sink.last | packet_full),
                # Intermediate beats are full, including a beat closing a split packet.
                packet_length.eq(counter*bytes_per_word + sink_bytes),

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
                source.length.eq(fifo.source.length),
            ]
            if with_be:
                self.comb += [
                    fifo.sink.be.eq(sink_be),
                    source.be.eq(fifo.source.be),
                ]
            else:
                self.comb += source.be.eq(full_be)

            self.sync += [
                If(sink.valid & sink.ready,
                    If(packet_last,
                        counter.eq(0)
                    ).Else(
                        counter.eq(counter + 1)
                    )
                ),
            ]

        # Hold the destination from the first presented beat through the final transfer. Disabling
        # TX prevents the next packet from starting, but must not withdraw an already valid packet.
        self.comb += [
            source.src_port.eq(Mux(source_active, _udp_port, self.udp_port)),
            source.dst_port.eq(Mux(source_active, _udp_port, self.udp_port)),
            source.ip_address.eq(Mux(source_active, _ip_address, self.ip_address)),
        ]
        self.sync += [
            If(source.valid & ~source_active,
                source_active.eq(1),
                _ip_address.eq(self.ip_address),
                _udp_port.eq(self.udp_port),
            ),
            If(source.valid & source.ready & source.last,
                source_active.eq(0),
            ),
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
    payload as a data stream. When ``with_be`` is set, ``source`` carries the ``be``
    byte mask on every beat so byte-granular packet lengths
    are preserved.
    """
    def __init__(self, ip_address=0, udp_port=0, data_width=8, fifo_depth=None, with_broadcast=True,
        with_csr = False,
        with_be  = False,
    ):
        source_description = eth_tty_rx_description(data_width, with_be=with_be)
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

        # Sample the filter decision when the first beat is presented. Keep it stable under
        # backpressure and until last, even if software changes the configuration mid-packet.
        first    = Signal(reset=1)
        selected = Signal()
        accept   = Signal()
        self.comb += accept.eq(Mux(first, valid, selected))
        self.sync += If(sink.valid,
            If(first,
                selected.eq(valid),
                first.eq(0),
            ),
            If(sink.ready & sink.last,
                first.eq(1),
            ),
        )

        # Data-Path / Buffering (Optional).
        keep = {"last", "data"}
        if with_be:
            keep |= {"be"}
        if fifo_depth is None:
            self.comb += [
                sink.connect(source, keep=keep),
                source.error.eq((sink.error & sink.be) != 0),
                source.valid.eq(sink.valid & accept),
                sink.ready.eq(source.ready | ~accept)
            ]
        else:
            fifo_layout = [("data", data_width), ("error", 1)]
            if with_be:
                fifo_layout += [("be", data_width//8)]
            self.fifo = fifo = stream.SyncFIFO(
                layout   = fifo_layout,
                depth    = fifo_depth,
                buffered = True,
            )
            self.comb += [
                sink.connect(fifo.sink, keep=keep),
                fifo.sink.error.eq((sink.error & sink.be) != 0),
                fifo.sink.valid.eq(sink.valid & accept),
                sink.ready.eq(fifo.sink.ready | ~accept),
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
        with_broadcast       = True,
        cd                   = "sys",
        with_be              = False,
        tx_max_packet_length = None,
    ):
        self.tx = tx = LiteEthStream2UDPTX(ip_address, udp_port, data_width, tx_fifo_depth,
            with_be           = with_be,
            max_packet_length = tx_max_packet_length,
        )
        self.rx = rx = LiteEthUDP2StreamRX(ip_address, udp_port, data_width, rx_fifo_depth,
            with_broadcast = with_broadcast,
            with_be        = with_be,
        )
        udp_port = udp.crossbar.get_port(udp_port, dw=data_width, cd=cd)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source
