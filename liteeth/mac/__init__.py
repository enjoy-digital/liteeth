#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V. <goemansrowan@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from liteeth.common import *
from liteeth.mac.common import *
from liteeth.mac.core import LiteEthMACCore
from liteeth.mac.wishbone import LiteEthMACWishboneInterface

# MAC ----------------------------------------------------------------------------------------------

class LiteEthMAC(LiteXModule):
    def __init__(self, phy, dw,
        interface          = "crossbar",
        endianness         = "big",
        with_preamble_crc  = True,
        nrxslots           = 2,
        rxslots_read_only  = True,
        ntxslots           = 2,
        txslots_write_only = False,
        hw_mac             = None,
        timestamp          = None,
        full_memory_we     = False,
        with_sys_datapath  = False,
        tx_cdc_depth       = 32,
        tx_cdc_buffered    = False,
        rx_cdc_depth       = 32,
        rx_cdc_buffered    = False,
    ):
        assert dw%8 == 0
        assert interface  in ["crossbar", "wishbone", "hybrid"]
        assert endianness in ["big", "little"]

        self.core = LiteEthMACCore(
            phy               = phy,
            dw                = dw,
            with_sys_datapath = with_sys_datapath,
            with_preamble_crc = with_preamble_crc,
            tx_cdc_depth      = tx_cdc_depth,
            tx_cdc_buffered   = tx_cdc_buffered,
            rx_cdc_depth      = rx_cdc_depth,
            rx_cdc_buffered   = rx_cdc_buffered,
        )
        self.csrs = []
        if interface == "crossbar":
            self.crossbar     = LiteEthMACCrossbar(dw)
            self.packetizer   = LiteEthMACPacketizer(dw)
            self.depacketizer = LiteEthMACDepacketizer(dw)
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
            self.interface = wishbone_interface
            self.ev, self.bus_rx, self.bus_tx = self.interface.sram.ev, self.interface.bus_rx, self.interface.bus_tx
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
            if interface == "hybrid":
                # Hardware MAC
                self.crossbar     = LiteEthMACCrossbar(dw)
                self.mac_crossbar = LiteEthMACCoreCrossbar(self.core, self.crossbar, self.interface, dw, hw_mac)
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

        # IP core packet processing.
        self.packetizer   = LiteEthMACPacketizer(dw)
        self.depacketizer = LiteEthMACDepacketizer(dw)

        # HW Input Path.
        self.comb += [
            # Depacketizer -> Crossbar.
            self.depacketizer.source.connect(crossbar.master.sink),
        ]

        # HW Output Path.
        self.comb += [
            # Crossbar -> Packetizer.
            crossbar.master.source.connect(self.packetizer.sink),
        ]

        # MAC filtering.
        if hw_mac is not None:
            depacketizer   = LiteEthMACDepacketizer(dw)
            hw_packetizer  = LiteEthMACPacketizer(dw)
            cpu_packetizer = LiteEthMACPacketizer(dw)

            hw_fifo  = stream.SyncFIFO(eth_mac_description(dw), depth=4, buffered=True)
            cpu_fifo = stream.SyncFIFO(eth_mac_description(dw), depth=4, buffered=True)

            self.submodules += depacketizer, cpu_packetizer, hw_packetizer, hw_fifo, cpu_fifo

            # Core -> Depacketizer.
            self.comb += core.source.connect(depacketizer.sink)


            # HW FIFO -> HW Packetizer -> Depacketizer.
            self.comb += [
                hw_fifo.source.connect(hw_packetizer.sink),
                hw_packetizer.source.connect(self.depacketizer.sink),
            ]

            # CPU FIFO -> CPU Packetizer -> Interface.
            self.comb += [
                cpu_fifo.source.connect(cpu_packetizer.sink),
                cpu_packetizer.source.connect(interface.sink),
            ]

            # RX Packetizer Broadcast Filtering.
            mac_local  = Signal() # Matches the Hardware MAC address (local).
            mac_bcast  = Signal() # Matches the Broadcast MAC address (FF:FF:FF:FF:FF:FF).
            mac_mcast4 = Signal() # Matches IPv4 Multicast MAC addresses (01:00:5E:XX:XX:XX).
            mac_mcast6 = Signal() # Matches IPv6 Multicast MAC addresses (33:33:XX:XX:XX:XX).
            mac_valid  = Signal() # Matches any of the above MAC types.
            self.comb += [
                # Hardware MAC address check.
                mac_local.eq(hw_mac == depacketizer.source.payload.target_mac),
                # Broadcast MAC address check.
                mac_bcast.eq( 0xffffffffffff == depacketizer.source.payload.target_mac),
                # IPv4 Multicast MAC address check.
                mac_mcast4.eq(0x01005e000000 == (depacketizer.source.payload.target_mac & 0xffffff000000)),
                # IPV6 Multicat MAC address check.
                mac_mcast6.eq(0x333300000000 == (depacketizer.source.payload.target_mac & 0xffff00000000)),
                # Combine all conditions to determine if the packet should be processed.
                mac_valid.eq(mac_local | mac_bcast | mac_mcast4 | mac_mcast6),

                # Accept when both FIFOs are ready.
                rx_ready.eq(hw_fifo.sink.ready & cpu_fifo.sink.ready),

                # Present when ready and Depacketizer valid.
                rx_valid.eq(rx_ready & depacketizer.source.valid),

                # Depacketizer -> HW FIFO/CPU FIFO.
                depacketizer.source.connect(hw_fifo.sink,  omit={"ready", "valid"}),
                depacketizer.source.connect(cpu_fifo.sink, omit={"ready", "valid"}),
                depacketizer.source.ready.eq(rx_ready),
                hw_fifo.sink.valid.eq(rx_valid & mac_valid),
                cpu_fifo.sink.valid.eq(rx_valid & ~mac_local),
            ]
        else:
            # RX Broadcast.
            self.comb += [
                # Accept when both Interface/Depacketizer are ready.
                rx_ready.eq(interface.sink.ready & self.depacketizer.sink.ready),

                # Present when ready and Core valid.
                rx_valid.eq(rx_ready & core.source.valid),

                # Core -> Interface/Depacketizer.
                core.source.connect(interface.sink,    omit={"ready", "valid"}),
                core.source.connect(depacketizer.sink, omit={"ready", "valid"}),
                core.source.ready.eq(rx_ready),
                interface.sink.valid.eq(rx_valid),
                self.depacketizer.sink.valid.eq(rx_valid),
            ]

        # TX arbiter FSM.
        self.tx_arb_fsm = tx_arb_fsm = FSM(reset_state="IDLE")
        tx_arb_fsm.act("IDLE",
            If(interface.source.valid,
                NextState("WISHBONE")
            ).Else(
                If(self.packetizer.source.valid,
                    NextState("CROSSBAR")
                )
            ),
        )
        tx_arb_fsm.act("WISHBONE",
            interface.source.connect(core.sink),
            If(core.sink.valid & core.sink.ready & core.sink.last,
                NextState("IDLE")
            ),
        )
        tx_arb_fsm.act("CROSSBAR",
            self.packetizer.source.connect(core.sink),
            If(core.sink.valid & core.sink.ready & core.sink.last,
                NextState("IDLE")
            ),
        )
