from liteeth.common import *
from liteeth.mac.common import *
from liteeth.mac.core import LiteEthMACCore
from liteeth.mac.wishbone import LiteEthMACWishboneInterface


class LiteEthMAC(Module, AutoCSR):
    def __init__(self, phy, dw,
                 interface="crossbar",
                 endianness="big",
                 with_preamble_crc=True,
                 nrxslots=2,
                 ntxslots=2,
                 cpu_dw=32,
                 hw_mac=None):
        self.submodules.core = LiteEthMACCore(phy, dw, endianness, with_preamble_crc)
        self.csrs = []
        if interface == "crossbar":
            self.submodules.crossbar = LiteEthMACCrossbar(dw)
            self.submodules.packetizer = LiteEthMACPacketizer(dw)
            self.submodules.depacketizer = LiteEthMACDepacketizer(dw)
            self.comb += [
                self.crossbar.master.source.connect(self.packetizer.sink),
                self.packetizer.source.connect(self.core.sink),
                self.core.source.connect(self.depacketizer.sink),
                self.depacketizer.source.connect(self.crossbar.master.sink)
            ]
        elif interface == "wishbone":
            self.rx_slots = CSRConstant(nrxslots)
            self.tx_slots = CSRConstant(ntxslots)
            self.slot_size = CSRConstant(2**bits_for(eth_mtu))
            self.submodules.interface = LiteEthMACWishboneInterface(dw, nrxslots, ntxslots, endianness)
            self.comb += Port.connect(self.interface, self.core)
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
        elif interface == "hybrid":
            # Wishbone MAC
            self.rx_slots = CSRConstant(nrxslots)
            self.tx_slots = CSRConstant(ntxslots)
            self.slot_size = CSRConstant(2**bits_for(eth_mtu))
            self.submodules.interface = LiteEthMACWishboneInterface(cpu_dw, nrxslots, ntxslots, endianness)
            # HW accelerated MAC
            self.submodules.crossbar = LiteEthMACCrossbar(dw)
            # MAC crossbar
            self.submodules.mac_crossbar = LiteEthMACCoreCrossbar(self.core, self.crossbar, self.interface, dw, cpu_dw, endianness, hw_mac)
            # Connections
            self.ev, self.bus = self.interface.sram.ev, self.interface.bus
            self.csrs = self.interface.get_csrs() + self.core.get_csrs()
        else:
            raise NotImplementedError

    def get_csrs(self):
        return self.csrs

class LiteEthMACCoreCrossbar(Module):
    def __init__(self, core, crossbar, interface, dw, cpu_dw, endianness, hw_mac=None):
        fifo_depth = 2048
        wishbone_rx_fifo = stream.SyncFIFO(eth_phy_description(dw), fifo_depth, buffered=True)
        wishbone_tx_fifo = stream.SyncFIFO(eth_phy_description(dw), fifo_depth, buffered=True)
        crossbar_rx_fifo = stream.SyncFIFO(eth_phy_description(dw), fifo_depth, buffered=True)
        crossbar_tx_fifo = stream.SyncFIFO(eth_phy_description(dw), fifo_depth, buffered=True)

        self.submodules += wishbone_rx_fifo, wishbone_tx_fifo, crossbar_rx_fifo, crossbar_tx_fifo

        rx_ready = Signal()
        rx_valid = Signal()

        reverse = endianness == "big"

        tx_pipe = []
        rx_pipe = []

        if cpu_dw != 8:
            tx_last_be = last_be.LiteEthMACTXLastBE(dw)
            rx_last_be = last_be.LiteEthMACRXLastBE(dw)

            tx_pipe += [tx_last_be]
            rx_pipe += [rx_last_be]

            self.submodules += tx_last_be, rx_last_be

        if dw != cpu_dw:
            tx_converter = stream.StrideConverter(eth_phy_description(cpu_dw),
                                                  eth_phy_description(dw),
                                                  reverse=reverse)
            rx_converter = stream.StrideConverter(eth_phy_description(dw),
                                                  eth_phy_description(cpu_dw),
                                                  reverse=reverse)
            rx_pipe += [rx_converter]
            tx_pipe += [tx_converter]

            self.submodules += tx_converter, rx_converter

        # SoftCPU packet processing
        self.submodules.tx_pipe = stream.Pipeline(*reversed(tx_pipe))
        self.submodules.rx_pipe = stream.Pipeline(*rx_pipe)
        # IP core packet processing
        self.submodules.packetizer = LiteEthMACPacketizer(dw)
        self.submodules.depacketizer = LiteEthMACDepacketizer(dw)

        self.comb += [
            # SoftCPU output path
            # interface -> tx_pipe -> tx_fifo
            interface.source.connect(self.tx_pipe.sink),
            self.tx_pipe.source.connect(wishbone_tx_fifo.sink),
            # SoftCPU input path
            # rx_fifo -> rx_pipe -> interface
            wishbone_rx_fifo.source.connect(self.rx_pipe.sink),
            self.rx_pipe.source.connect(interface.sink),
            # HW input path
            # rx_fifo -> depacketizer -> crossbar
            crossbar_rx_fifo.source.connect(self.depacketizer.sink),
            self.depacketizer.source.connect(crossbar.master.sink),
            # HW output path
            # crossbar -> packetizer -> tx_fifo
            crossbar.master.source.connect(self.packetizer.sink),
            self.packetizer.source.connect(crossbar_tx_fifo.sink),
        ]

        # MAC filtering
        if hw_mac is not None:
            depacketizer   = LiteEthMACDepacketizer(dw)
            hw_packetizer  = LiteEthMACPacketizer(dw)
            cpu_packetizer = LiteEthMACPacketizer(dw)

            fifo_depth = 4

            hw_fifo = stream.SyncFIFO(eth_mac_description(dw), fifo_depth, buffered=True)
            cpu_fifo = stream.SyncFIFO(eth_mac_description(dw), fifo_depth, buffered=True)

            self.submodules += depacketizer, cpu_packetizer, hw_packetizer, hw_fifo, cpu_fifo

            self.comb += [
                core.source.connect(depacketizer.sink),
                hw_fifo.source.connect(hw_packetizer.sink),
                hw_packetizer.source.connect(crossbar_rx_fifo.sink),
                cpu_fifo.source.connect(cpu_packetizer.sink),
                cpu_packetizer.source.connect(wishbone_rx_fifo.sink),
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
                rx_ready.eq(wishbone_rx_fifo.sink.ready & crossbar_rx_fifo.sink.ready),
                rx_valid.eq(rx_ready & core.source.valid),
                core.source.connect(wishbone_rx_fifo.sink, omit={"ready", "valid"}),
                core.source.connect(crossbar_rx_fifo.sink, omit={"ready", "valid"}),
                core.source.ready.eq(rx_ready),
                wishbone_rx_fifo.sink.valid.eq(rx_valid),
                crossbar_rx_fifo.sink.valid.eq(rx_valid),
            ]

        # TX arbiter
        self.submodules.tx_arbiter_fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(wishbone_tx_fifo.source.valid,
                wishbone_tx_fifo.source.connect(core.sink),
                NextState("WISHBONE")
            ).Else(
                If(crossbar_tx_fifo.source.valid,
                    crossbar_tx_fifo.source.connect(core.sink),
                    NextState("CROSSBAR")
                )
            ),
        )
        fsm.act("WISHBONE",
            wishbone_tx_fifo.source.connect(core.sink),
            If(wishbone_tx_fifo.source.valid & core.sink.ready & wishbone_tx_fifo.source.last,
                NextState("IDLE")
            ),
        )
        fsm.act("CROSSBAR",
            crossbar_tx_fifo.source.connect(core.sink),
            If(crossbar_tx_fifo.source.valid & core.sink.ready & crossbar_tx_fifo.source.last,
                NextState("IDLE")
            ),
        )
