#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream

# Packet Drop FIFO ---------------------------------------------------------------------------------

class PacketDropFIFO(LiteXModule):
    """Store-and-forward packet FIFO that drop whole packets instead of applying backpressure."""
    def __init__(self, layout, payload_depth, param_depth=4):
        self.sink   = sink   = stream.Endpoint(layout)
        self.source = source = stream.Endpoint(layout)
        self.drop   = Signal() # For diagnostics, pulsed per packet dropped.

        # # #

        assert payload_depth == 2**log2_int(payload_depth), "payload_depth must be a power of two."
        addr_bits = log2_int(payload_depth)

        param_layout = sink.description.param_layout
        if param_layout == []:
            param_layout = [("dummy", 1)]
        self.param_fifo = param_fifo = stream.SyncFIFO(
            stream.EndpointDescription(param_layout=param_layout), param_depth)

        data_bits = len(sink.payload.raw_bits())
        mem     = Memory(data_bits + 2, payload_depth)
        wr_port = mem.get_port(write_capable=True)
        rd_port = mem.get_port(has_re=True)
        self.specials += mem, wr_port, rd_port

        wr_ptr     = Signal(addr_bits + 1) # Next slot to write, uncommitted words included.
        commit_ptr = Signal(addr_bits + 1) # One past the last committed word.
        rd_ptr     = Signal(addr_bits + 1) # Next word to fetch out of the memory.
        out_valid  = Signal()              # rd_port.dat_r holds a fetched word.
        used       = Signal(addr_bits + 1)
        full       = Signal()
        overflow   = Signal()              # This packet has already lost a word.
        commit     = Signal()
        fetch      = Signal()

        self.comb += [
            used.eq(wr_ptr - (rd_ptr - out_valid)),
            full.eq(used == payload_depth),
            sink.ready.eq(1), # Cannot backpressure!
        ]

        # Write side
        # ----------
        self.comb += [
            wr_port.adr.eq(wr_ptr[:addr_bits]),
            wr_port.dat_w.eq(Cat(sink.payload.raw_bits(), sink.first, sink.last)),
            wr_port.we.eq(sink.valid & ~overflow & ~full),
            # Keep the packet only if every word of it was stored with parameters.
            commit.eq(sink.valid & sink.last & ~overflow & ~full & param_fifo.sink.ready),
            sink.connect(param_fifo.sink, keep=set([e[0] for e in param_layout])),
            param_fifo.sink.valid.eq(commit),
            param_fifo.sink.last.eq(1),
            self.drop.eq(sink.valid & sink.last & ~commit),
        ]
        self.sync += [
            If(sink.valid,
                If(~overflow & ~full,
                    wr_ptr.eq(wr_ptr + 1),
                ).Else(
                    overflow.eq(1),
                ),
                If(sink.last,
                    overflow.eq(0),
                    If(commit,
                        commit_ptr.eq(wr_ptr + 1),
                    ).Else(
                        # Rewind: as far as the read side knows, this packet never arrived.
                        wr_ptr.eq(commit_ptr),
                    ),
                ),
            ),
        ]

        # Read side
        # ---------
        self.comb += [
            fetch.eq((rd_ptr != commit_ptr) & (~out_valid | (source.valid & source.ready))),
            rd_port.adr.eq(rd_ptr[:addr_bits]),
            rd_port.re.eq(fetch),
            param_fifo.source.connect(source, omit={"valid", "ready", "first", "last", "dummy"}),
            source.payload.raw_bits().eq(rd_port.dat_r[:data_bits]),
            source.first.eq(rd_port.dat_r[data_bits + 0]),
            source.last.eq( rd_port.dat_r[data_bits + 1]),
            source.valid.eq(out_valid & param_fifo.source.valid),
            param_fifo.source.ready.eq(source.valid & source.last & source.ready),
        ]
        self.sync += [
            If(fetch,
                rd_ptr.eq(rd_ptr + 1),
                out_valid.eq(1),
            ).Elif(source.valid & source.ready,
                out_valid.eq(0),
            ),
        ]
