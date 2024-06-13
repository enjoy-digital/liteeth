from migen import *

from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

from liteeth.common import *

from litedram.frontend.dma import LiteDRAMDMAWriter


class LiteEthMACDMAWriter(Module, AutoCSR):
    def __init__(self, dw, nslots, depth, port, offset):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))

        dma_dw = port.data_width
        length_bits = bits_for(depth - 1)
        slot_bits = bits_for(nslots - 1)

        self._slot   = CSRStatus(slot_bits)
        self._length = CSRStatus(length_bits)
        self._errors = CSRStatus(32)

        self.submodules.ev = EventManager()
        self.ev.available  = EventSourceLevel()
        self.ev.finalize()

        # # #

        errors = self._errors.status

        slot       = Signal(slot_bits)
        length     = Signal(length_bits, reset_less=True)
        length_inc = Signal(bits_for(dw//8))

        # Decode Length increment from from last_be.
        self.comb += Case(sink.last_be, {
            0b00000001 : length_inc.eq(1),
            0b00000010 : length_inc.eq(2),
            0b00000100 : length_inc.eq(3),
            0b00001000 : length_inc.eq(4),
            0b00010000 : length_inc.eq(5),
            0b00100000 : length_inc.eq(6),
            0b01000000 : length_inc.eq(7),
            "default"  : length_inc.eq(dw//8)
        })

        # Status FIFO.
        _stat_fifo_layout = [("slot", slot_bits), ("length", length_bits)]
        stat_fifo = stream.SyncFIFO(_stat_fifo_layout, nslots)
        self.submodules += stat_fifo

        # Converter.
        conv = stream.StrideConverter(
            description_from=[("data", dw)],
            description_to=[("data", dma_dw)])
        conv = ResetInserter()(conv)
        self.submodules += conv

        # DMA.
        start = Signal()
        done  = Signal()

        self.submodules.dma = dma = LiteDRAMDMAWriter(port)

        self.comb += conv.source.connect(dma.sink, omit={"address"}),

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            conv.reset.eq(1),
            If(sink.valid,
                If(stat_fifo.sink.ready,
                    start.eq(1),
                    NextValue(length, 0),
                    NextState("WRITE")
                ).Else(
                    NextValue(errors, errors + 1),
                    NextState("DISCARD-REMAINING")
                )
            )
        )
        fsm.act("WRITE",
            sink.connect(conv.sink, omit={"last_be", "error"}),
            If(sink.valid & sink.ready,
                NextValue(length, length + length_inc),
                If(sink.last,
                    NextState("TERMINATE")
                ).Elif(length >= eth_mtu,
                    NextState("DISCARD-REMAINING")
                )
            )
        )
        fsm.act("DISCARD-REMAINING",
            sink.ready.eq(1),
            If(sink.valid & sink.last,
                NextState("IDLE")
            )
        )
        fsm.act("TERMINATE",
            If(done,
                stat_fifo.sink.valid.eq(1),
                NextValue(slot, slot + 1),
                NextState("IDLE")
            )
        )

        self.comb += [
            stat_fifo.sink.slot.eq(slot),
            stat_fifo.sink.length.eq(length),
            stat_fifo.source.ready.eq(self.ev.available.clear),
            self.ev.available.trigger.eq(stat_fifo.source.valid),
            self._slot.status.eq(stat_fifo.source.slot),
            self._length.status.eq(stat_fifo.source.length),
        ]

        # DMA address.
        wr_addr_offset = C(offset // (dma_dw//8))
        wr_addr = Signal(bits_for(depth // (dma_dw//8) - 1), reset_less=True)

        wr_slot_offset = Signal.like(dma.sink.address)
        self.comb += wr_slot_offset.eq(slot * (depth // (dma_dw//8)))

        self.comb += dma.sink.address.eq(wr_addr_offset + wr_slot_offset + wr_addr)

        self.submodules.dma_fsm = dma_fsm = FSM(reset_state="IDLE")
        dma_fsm.act("IDLE",
            done.eq(1),
            If(start,
                NextValue(wr_addr, 0),
                NextState("WRITE")
            )
        )
        dma_fsm.act("WRITE",
            If(dma.sink.valid & dma.sink.ready,
                NextValue(wr_addr, wr_addr + 1),
                If(dma.sink.last,
                    NextState("IDLE")
                )
            )
        )
