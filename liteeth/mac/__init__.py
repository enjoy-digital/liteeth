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
        nrxslots          = 2, rxslots_read_only  = True,
        ntxslots          = 2, txslots_write_only = False,
        hw_mac            = None,
        timestamp         = None,
        full_memory_we    = False,
        with_sys_datapath = False):

        assert dw%8 == 0
        assert interface  in ["crossbar", "wishbone", "hybrid"]
        assert endianness in ["big", "little"]

        self.submodules.core = LiteEthMACCore(
            phy               = phy,
            dw                = dw,
            with_sys_datapath = with_sys_datapath,
            with_preamble_crc = with_preamble_crc
        )
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
            wishbone_interface = LiteEthMACWishboneInterface(
                dw         = dw,
                nrxslots   = nrxslots, rxslots_read_only  = rxslots_read_only,
                ntxslots   = ntxslots, txslots_write_only = txslots_write_only,
                endianness = endianness,
                timestamp  = timestamp,
            )
            # On some targets (Intel/Altera), the complex ports aren't inferred
            # as block ram, but are created with LUTs.  FullMemoryWe splits such
            # `Memory` instances up into 4 separate memory blocks, each
            # containing 8 bits which gets inferred correctly on intel/altera.
            # Yosys on ECP5 inferrs the original correctly, so FullMemoryWE
            # leads to additional block ram instances being used, which
            # increases memory usage by a lot.
            if full_memory_we:
                wishbone_interface = FullMemoryWE()(wishbone_interface)
            self.submodules.interface = wishbone_interface
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
            if interface == "hybrid":
                # Hardware MAC
                self.submodules.crossbar     = LiteEthMACCrossbar(dw)
                self.submodules.mac_crossbar = LiteEthMACCoreCrossbar(self.core, self.crossbar, self.interface, dw, hw_mac)
            else:
                self.comb += self.interface.source.connect(self.core.sink)
                self.comb += self.core.source.connect(self.interface.sink)

    def get_csrs(self):
        return self.csrs

# MAC Core Crossbar --------------------------------------------------------------------------------

class LiteEthMACCoreCrossbar(Module):
    def __init__(self, core, crossbar, interface, dw, hw_mac=None):
        rx_ready = Signal()
        rx_valid = Signal()

        # IP core packet processing
        self.submodules.packetizer   = LiteEthMACPacketizer(dw)
        self.submodules.depacketizer = LiteEthMACDepacketizer(dw)

        self.comb += [
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
                cpu_packetizer.source.connect(interface.sink),
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
                rx_ready.eq(interface.sink.ready & self.depacketizer.sink.ready),
                rx_valid.eq(rx_ready & core.source.valid),
                core.source.connect(interface.sink, omit={"ready", "valid"}),
                core.source.connect(self.depacketizer.sink, omit={"ready", "valid"}),
                core.source.ready.eq(rx_ready),
                interface.sink.valid.eq(rx_valid),
                self.depacketizer.sink.valid.eq(rx_valid),
            ]

        # TX arbiter
        self.submodules.tx_arbiter_fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(interface.source.valid,
                NextState("WISHBONE")
            ).Else(
                If(self.packetizer.source.valid,
                    NextState("CROSSBAR")
                )
            ),
        )
        fsm.act("WISHBONE",
            interface.source.connect(core.sink),
            If(core.sink.valid & core.sink.ready & core.sink.last,
                NextState("IDLE")
            ),
        )
        fsm.act("CROSSBAR",
            self.packetizer.source.connect(core.sink),
            If(core.sink.valid & core.sink.ready & core.sink.last,
                NextState("IDLE")
            ),
        )
