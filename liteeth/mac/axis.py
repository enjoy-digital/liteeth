#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.interconnect.axi import AXIStreamInterface
from litex.soc.interconnect.packet import PacketFIFO

from liteeth.common import *
from liteeth.fifo import PacketDropFIFO
from liteeth.mac.padding import LiteEthMACPaddingInserter

# Hardware MAC Stream Adapter ----------------------------------------------------------------------

class LiteEthMACAXIStream(LiteXModule):
    """Adapt a frame-oriented hardware MAC to LiteEth's PHY-facing stream.

    AXI frames start at destination MAC and omit FCS. The adapter pads TX frames; the hardware
    MAC owns FCS, preamble and IFG. TX supports ready; RX has no backpressure. All TX signals belong to
    eth_tx and RX signals/counters to eth_rx. The parent owns clocks and resets.
    """
    with_preamble_crc       = False
    with_padding           = False
    integrated_ifg_inserter = True
    with_store_and_forward = False # These queues replace MACCore's PHY-side queues.

    def __init__(self, dw=512, clk_freq=322.265625e6, eth_mtu=eth_mtu_jumboframe,
        tx_fifo_depth=256, rx_fifo_depth=256, param_depth=16):
        if dw not in [64, 128, 256, 512]:
            raise ValueError("Hardware MAC streams require 64/128/256/512-bit words.")
        if eth_mtu < eth_min_frame_length:
            raise ValueError("The MTU must accommodate a minimum Ethernet frame.")
        max_length = eth_mtu - eth_fcs_length
        min_depth  = (max_length + dw//8 - 1)//(dw//8)
        if tx_fifo_depth < min_depth or rx_fifo_depth < min_depth:
            raise ValueError("Each MAC queue must hold a complete maximum-sized frame.")
        if rx_fifo_depth < 2 or rx_fifo_depth & (rx_fifo_depth - 1):
            raise ValueError("RX queue depth must be a power of two, at least two words.")
        if param_depth < 2:
            raise ValueError("At least two frame descriptors are required.")
        self.dw          = dw
        self.tx_clk_freq = clk_freq
        self.rx_clk_freq = clk_freq
        self.sink        = stream.Endpoint(eth_phy_description(dw))
        self.source      = stream.Endpoint(eth_phy_description(dw))
        self.tx          = AXIStreamInterface(dw, user_width=1, clock_domain="eth_tx")
        self.rx          = AXIStreamInterface(dw, user_width=1, clock_domain="eth_rx")
        self.link_up     = Signal(reset=1)
        self.rx_packets  = Signal(32)
        self.rx_drops    = Signal(32)
        self.rx_bad_frames = Signal(32)

        # # #

        # TX: commit a complete frame before exposing it to an unpausable wire transmitter.
        # The caller must finish frames within eth_mtu, as for MACCore's existing PacketFIFO.
        self.tx_padding = tx_padding = ClockDomainsRenamer("eth_tx")(
            LiteEthMACPaddingInserter(dw, eth_min_frame_length - eth_fcs_length))
        self.tx_fifo = tx_fifo = ClockDomainsRenamer("eth_tx")(PacketFIFO(
            eth_phy_description(dw), payload_depth=tx_fifo_depth, param_depth=param_depth, buffered=True))
        tx = tx_fifo.source
        full = (1 << (dw//8)) - 1
        tx_bad = Signal()
        tx_bad_beat = Signal()
        input_bad = Signal()
        self.comb += [
            self.sink.connect(tx_padding.sink),
            # Preserve malformed input masks as an error even if padding fills their holes.
            input_bad.eq((self.sink.be == 0) | ((self.sink.be & (self.sink.be + 1)) != 0) |
                (~self.sink.last & (self.sink.be != full))),
            tx_padding.sink.error.eq(self.sink.error | Replicate(input_bad, dw//8)),
            tx_padding.source.connect(tx_fifo.sink),
            tx_bad_beat.eq((tx.be == 0) | ((tx.be & (tx.be + 1)) != 0) |
                (~tx.last & (tx.be != full)) | ((tx.error & tx.be) != 0)),
            self.tx.valid.eq(tx.valid),
            tx.ready.eq(self.tx.ready),
            self.tx.data.eq(tx.data),
            self.tx.keep.eq(tx.be),
            self.tx.last.eq(tx.last),
            self.tx.user.eq(tx.last & (tx_bad | tx_bad_beat)),
        ]
        self.sync.eth_tx += If(tx.valid & tx.ready,
            tx_bad.eq(~tx.last & (tx_bad | tx_bad_beat)),
        )

        # RX: an overflow or late error must discard the entire frame, never deliver its prefix.
        self.rx_fifo = rx_fifo = ClockDomainsRenamer("eth_rx")(PacketDropFIFO(
            eth_phy_description(dw), payload_depth=rx_fifo_depth, param_depth=param_depth))
        count = Signal(max=max_length + dw//8 + 1)
        size  = Signal.like(count)
        bad   = Signal()
        bad_beat = Signal()
        self.comb += [
            self.rx.ready.eq(1), # There is no corresponding port on CMAC.
            rx_fifo.sink.valid.eq(self.rx.valid),
            rx_fifo.sink.data.eq(self.rx.data),
            rx_fifo.sink.be.eq(self.rx.keep),
            rx_fifo.sink.last.eq(self.rx.last),
            size.eq(count + stream.byte_count(self.rx.keep)),
            bad_beat.eq(self.rx.user | (self.rx.keep == 0) |
                ((self.rx.keep & (self.rx.keep + 1)) != 0) |
                (~self.rx.last & (self.rx.keep != full)) | (size > max_length) |
                (self.rx.last & (size < eth_min_frame_length - eth_fcs_length))),
            rx_fifo.discard.eq(bad_beat),
            rx_fifo.source.connect(self.source),
        ]
        self.sync.eth_rx += [
            If(self.rx.valid,
                If(self.rx.last,
                    count.eq(0),
                    bad.eq(0),
                    self.rx_packets.eq(self.rx_packets + 1),
                    If(bad | bad_beat, self.rx_bad_frames.eq(self.rx_bad_frames + 1)),
                ).Else(
                    bad.eq(bad | bad_beat),
                    If(count <= max_length, count.eq(size)),
                ),
            ),
            If(rx_fifo.drop, self.rx_drops.eq(self.rx_drops + 1)),
        ]
