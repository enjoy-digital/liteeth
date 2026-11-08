from migen import *

from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

from liteeth.common import *

from litedram.frontend.dma import LiteDRAMDMAReader


class LiteEthMACDMAReader(Module, AutoCSR):
    def __init__(self, dw, nslots, depth, port, offset):
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        dma_dw = port.data_width
        length_bits = bits_for(depth - 1)
        slot_bits = bits_for(nslots - 1)

        self._start  = CSR()
        self._ready  = CSRStatus()
        self._slot   = CSRStorage(slot_bits)
        self._length = CSRStorage(length_bits)

        self.submodules.ev = EventManager()
        self.ev.done = EventSourcePulse()
        self.ev.finalize()

        # # #

        # Command FIFO.
        _cmd_fifo_layout = [("slot", slot_bits), ("length", length_bits)]
        cmd_fifo = stream.SyncFIFO(_cmd_fifo_layout, nslots)
        self.submodules += cmd_fifo
        self.comb += [
            cmd_fifo.sink.valid.eq(self._start.re & self._start.r),
            cmd_fifo.sink.slot.eq(self._slot.storage),
            cmd_fifo.sink.length.eq(self._length.storage),
            self._ready.status.eq(cmd_fifo.sink.ready),
        ]

        length = cmd_fifo.source.length
        count  = Signal(length_bits, reset_less=True)

        # Encode Length to last_be.
        length_lsb = length[:log2_int(dw//8)] if (dw != 8) else 0
        self.comb += If(source.last,
            Case(length_lsb, {
                1         : source.last_be.eq(0b00000001),
                2         : source.last_be.eq(0b00000010),
                3         : source.last_be.eq(0b00000100),
                4         : source.last_be.eq(0b00001000),
                5         : source.last_be.eq(0b00010000),
                6         : source.last_be.eq(0b00100000),
                7         : source.last_be.eq(0b01000000),
                "default" : source.last_be.eq(2**(dw//8 - 1)),
            })
        )

        # DMA.
        start = Signal()
        last  = Signal()

        self.submodules.dma = dma = LiteDRAMDMAReader(port)

        # Converter.
        conv = stream.StrideConverter(
            description_from=[("data", dma_dw)],
            description_to=[("data", dw)])
        conv = ResetInserter()(conv)
        self.submodules += conv

        self.comb += conv.source.connect(source, omit={"last", "last_be"}),

        # FSM.
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            conv.reset.eq(1),
            If(cmd_fifo.source.valid,
                start.eq(1),
                NextValue(count, 0),
                NextState("READ")
            )
        )
        fsm.act("READ",
            dma.source.connect(conv.sink),
            source.last.eq(count >= length - 1),
            If(source.valid & source.ready,
                NextValue(count, count + (dw//8)),
                If(source.last,
                    last.eq(1),
                    NextState("TERMINATE")
                )
            )
        )
        fsm.act("TERMINATE",
            dma.source.ready.eq(1),
            If(dma.rsv_level == 0,
                self.ev.done.trigger.eq(1),
                cmd_fifo.source.ready.eq(1),
                NextState("IDLE")
            )
        )

        # DMA address.
        rd_addr_offset = C(offset // (dma_dw//8))
        rd_addr = Signal(bits_for(depth // (dma_dw//8) - 1), reset_less=True)

        rd_slot = cmd_fifo.source.slot
        rd_slot_offset = Signal.like(dma.sink.address)
        self.comb += rd_slot_offset.eq(rd_slot * (depth // (dma_dw//8)))

        self.comb += dma.sink.address.eq(rd_addr_offset + rd_slot_offset + rd_addr)

        self.submodules.dma_fsm = dma_fsm = FSM(reset_state="IDLE")
        dma_fsm.act("IDLE",
            If(start,
                NextValue(rd_addr, 0),
                NextState("READ")
            )
        )
        dma_fsm.act("READ",
            dma.sink.valid.eq(1),
            If(dma.sink.valid & dma.sink.ready,
                NextValue(rd_addr, rd_addr + 1)
            ),
            If(last,
                NextState("IDLE")
            )
        )
