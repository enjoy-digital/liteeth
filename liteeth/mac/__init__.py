#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *
from liteeth.mac.common import *
from liteeth.mac.core import LiteEthMACCore
from liteeth.mac.wishbone import LiteEthMACWishboneInterface

# MAC ----------------------------------------------------------------------------------------------

class LiteEthMAC(Module, AutoCSR):
    def __init__(self, phy, dw,
        interface         = "crossbar",
        endianness        = "big",
        with_preamble_crc = True,
        nrxslots          = 2,
        ntxslots          = 2,
        hw_mac            = None):
        assert interface in ["crossbar", "wishbone", "hybrid"]
        self.submodules.core = LiteEthMACCore(phy, dw, endianness, with_preamble_crc)
        self.csrs = []
        if interface == "crossbar":
            self.submodules.crossbar     = LiteEthMACCrossbar(dw)
            self.submodules.packetizer   = LiteEthMACPacketizer(dw)
            self.submodules.depacketizer = LiteEthMACDepacketizer(dw)
            self.comb += [
                self.crossbar.master.source.connect(self.packetizer.sink),
                self.packetizer.source.connect(self.core.sink),
                self.core.source.connect(self.depacketizer.sink),
                self.depacketizer.source.connect(self.crossbar.master.sink)
            ]
        else:
            # Wishbone MAC
            self.rx_slots  = CSRConstant(nrxslots)
            self.tx_slots  = CSRConstant(ntxslots)
            self.slot_size = CSRConstant(2**bits_for(eth_mtu))
            self.submodules.interface = LiteEthMACWishboneInterface(32, nrxslots, ntxslots, endianness)
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
            if interface == "hybrid":
                assert dw == 8
                # Hardware MAC
                self.submodules.crossbar     = LiteEthMACCrossbar(dw)
                self.submodules.mac_crossbar = LiteEthMACCoreCrossbar(self.core, self.crossbar, self.interface, dw, endianness, hw_mac)
            else:
                assert dw == 32
                self.comb += self.interface.source.connect(self.core.sink)
                self.comb += self.core.source.connect(self.interface.sink)

    def get_csrs(self):
        return self.csrs

# MAC Core Crossbar --------------------------------------------------------------------------------

class LiteEthMACCoreCrossbar(Module):
    def __init__(self, core, crossbar, interface, dw, endianness, hw_mac=None):
        rx_ready = Signal()
        rx_valid = Signal()

        reverse = endianness == "big"

        tx_pipe = []
        rx_pipe = []

        tx_last_be = last_be.LiteEthMACTXLastBE(dw)
        rx_last_be = last_be.LiteEthMACRXLastBE(dw)
        tx_pipe += [tx_last_be]
        rx_pipe += [rx_last_be]
        self.submodules += tx_last_be, rx_last_be

        tx_converter = stream.StrideConverter(
            description_from = eth_phy_description(32),
            description_to   = eth_phy_description(dw),
            reverse          = reverse)
        rx_converter = stream.StrideConverter(
            description_from = eth_phy_description(dw),
            description_to   = eth_phy_description(32),
            reverse          = reverse)
        rx_pipe += [rx_converter]
        tx_pipe += [tx_converter]
        self.submodules += tx_converter, rx_converter

        # CPU packet processing
        self.submodules.tx_pipe = stream.Pipeline(*reversed(tx_pipe))
        self.submodules.rx_pipe = stream.Pipeline(*rx_pipe)
        # IP core packet processing
        self.submodules.packetizer   = LiteEthMACPacketizer(dw)
        self.submodules.depacketizer = LiteEthMACDepacketizer(dw)

        self.comb += [
            # CPU output path
            # interface -> tx_pipe
            interface.source.connect(self.tx_pipe.sink),
            # CPU input path
            # rx_pipe -> interface
            self.rx_pipe.source.connect(interface.sink),
            # HW input path
            # depacketizer -> crossbar
            self.depacketizer.source.connect(crossbar.master.sink),
            # HW output path
            # crossbar -> packetizer -> tx_fifo
            crossbar.master.source.connect(self.packetizer.sink),
        ]

        # MAC filtering
        if hw_mac is not None:
            depacketizer   = LiteEthMACDepacketizer(dw)
            hw_packetizer  = LiteEthMACPacketizer(dw)
            cpu_packetizer = LiteEthMACPacketizer(dw)

            hw_fifo  = stream.SyncFIFO(eth_mac_description(dw), depth=4, buffered=True)
            cpu_fifo = stream.SyncFIFO(eth_mac_description(dw), depth=4, buffered=True)

            self.submodules += depacketizer, cpu_packetizer, hw_packetizer, hw_fifo, cpu_fifo

            self.comb += [
                core.source.connect(depacketizer.sink),
                hw_fifo.source.connect(hw_packetizer.sink),
                hw_packetizer.source.connect(self.depacketizer.sink),
                cpu_fifo.source.connect(cpu_packetizer.sink),
                cpu_packetizer.source.connect(self.rx_pipe.sink),
            ]

            # RX packetizer broadcast
            mac_match = Signal()
            self.comb += [
                mac_match.eq(hw_mac == depacketizer.source.payload.target_mac),
                rx_ready.eq(hw_fifo.sink.ready & (cpu_fifo.sink.ready | mac_match)),
                rx_valid.eq(rx_ready & depacketizer.source.valid),
                depacketizer.source.connect(hw_fifo.sink, omit={"ready", "valid"}),
                depacketizer.source.connect(cpu_fifo.sink, omit={"ready", "valid"}),
                depacketizer.source.ready.eq(rx_ready),
                hw_fifo.sink.valid.eq(rx_valid),
                cpu_fifo.sink.valid.eq(rx_valid & ~mac_match),
            ]
        else:
            # RX broadcast
            self.comb += [
                rx_ready.eq(self.rx_pipe.sink.ready & self.depacketizer.sink.ready),
                rx_valid.eq(rx_ready & core.source.valid),
                core.source.connect(self.rx_pipe.sink, omit={"ready", "valid"}),
                core.source.connect(self.depacketizer.sink, omit={"ready", "valid"}),
                core.source.ready.eq(rx_ready),
                self.rx_pipe.sink.valid.eq(rx_valid),
                self.depacketizer.sink.valid.eq(rx_valid),
            ]

        # TX arbiter
        self.submodules.tx_arbiter_fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.tx_pipe.source.valid,
                self.tx_pipe.source.connect(core.sink),
                NextState("WISHBONE")
            ).Else(
                If(self.packetizer.source.valid,
                    self.packetizer.source.connect(core.sink),
                    NextState("CROSSBAR")
                )
            ),
        )
        fsm.act("WISHBONE",
            self.tx_pipe.source.connect(core.sink),
            If(self.tx_pipe.source.valid & core.sink.ready & self.tx_pipe.source.last,
                NextState("IDLE")
            ),
        )
        fsm.act("CROSSBAR",
            self.packetizer.source.connect(core.sink),
            If(self.packetizer.source.valid & core.sink.ready & self.packetizer.source.last,
                NextState("IDLE")
            ),
        )
