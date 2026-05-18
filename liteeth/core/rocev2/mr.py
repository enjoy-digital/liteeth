from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import Buffer, Endpoint, SyncFIFO

from litedram.frontend.dma import LiteDRAMDMAReader, LiteDRAMDMAWriter

from litedram.common import LiteDRAMNativePort
from litedram.frontend.axi import LiteDRAMAXIPort
from migen.fhdl.specials import _MemoryPort

from enum import Flag, auto

class LiteBRAMReader(LiteXModule):
    def __init__(self, port):
        self.sink   = sink = Endpoint([("address", len(port.adr))])
        self.source = source = Endpoint([("data", len(port.dat_r))])

        self.outs = outs = SyncFIFO([("data", len(port.dat_r))], 32)
        self.submodules += outs
        self.comb += port.adr.eq(sink.address)

        self.fsm = fsm = FSM(reset_state="IDLE")
        self.fsm_state = fsm_state = Signal()
        fsm.act("IDLE",
            fsm_state.eq(0),
            If(sink.valid,
                NextState("READ")
            )
        )
        fsm.act("READ",
            fsm_state.eq(1),
            outs.sink.data.eq(port.dat_r),
            outs.sink.last.eq(sink.last),
            outs.sink.valid.eq(1),
            If(outs.sink.ready,
                sink.ready.eq(1),
                NextState("IDLE")
            )
        )

        self.comb += outs.source.connect(source)

class LiteBRAMWriter(LiteXModule):
    def __init__(self, port):
        self.sink   = sink = Endpoint([("address", len(port.adr)),
                                            ("data", len(port.dat_w))])

        self.comb += [
            sink.ready.eq(1),
            port.adr.eq(sink.address),
            port.we.eq(sink.valid),
            port.dat_w.eq(sink.data),
        ]


# Memory management --------------------------------------------------------------------------------
class LiteEthIBMemoryRegionReader(LiteXModule):
    """Infiniband Memory Region Reader

    A reader that accepts a virtual address and length and sends back the corresponding data from ram.
    Virtual address 0 corresponds to start of memory region.

    Parameters
    ----------
    region_start : int
        Physical address of the start
        of the memory region in the ram.
    region_size : int
        The size of the memory region.
    port : LiteDRAMNativePort
        Read port to physical sram.
    buffered_in : bool
        Whether to buffer input to cut timing
    buffered_out : bool
        Whether to buffer output to cut timing
    dw : int
        Width of the data bus.

    Attributes
    ----------
    sink : in
        Address and length of data to be read.
    """
    def __init__(self, region_start, region_size, port, buffered_in=True, buffered_out=True, dw=8):
        self.sink   = sink   = Endpoint([("va", 64), ("len", bits_for(PMTU))])
        self.source = source = Endpoint([("data", dw)])

        # # #

        # Bufferize sink to cut timing path
        if buffered_in:
            buff_in = Buffer([("va", 64), ("len", bits_for(PMTU))])
            self.submodules += buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        if buffered_out:
            buff_out = Buffer([("data", dw)])
            self.submodules += buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink

        if (isinstance(port, LiteDRAMNativePort) or isinstance(port, LiteDRAMAXIPort)):
            port_width = port.data_width
        elif isinstance(port, _MemoryPort):
            port_width = len(port.dat_r)
        ratio      = port_width // dw
        ratio_bits = log2_int(ratio)

        assert dw == 8
        assert port_width % dw == 0

        # Internal DMA reader
        if (isinstance(port, LiteDRAMNativePort) or isinstance(port, LiteDRAMAXIPort)):
            inner_reader = LiteDRAMDMAReader(port, fifo_depth=PMTU)
        elif isinstance(port, _MemoryPort):
            inner_reader = LiteBRAMReader(port)
        self.submodules += inner_reader

        # Address of word to be used for next read request
        word_address = Signal(64 - ratio_bits)
        # Byte being read from current returned word
        reading_byte_local_address = Signal(ratio_bits)
        # The number of the word being output from the DMA reader
        reading_byte = Signal(bits_for(PMTU))

        # Reader FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid,
                NextValue(word_address, (region_start + sink.va)[ratio_bits:]),
                NextValue(reading_byte_local_address, (region_start + sink.va)[:ratio_bits]),
                NextValue(reading_byte, 1),
                NextState("READING")
            )
        )

        fsm.act("READING",
            # Pipe in requests
            inner_reader.sink.address.eq(word_address),
            inner_reader.sink.valid.eq(1),
            inner_reader.sink.last.eq(word_address == (region_start + sink.va + sink.len - 1)[ratio_bits:]),
            If(inner_reader.sink.ready,
                NextValue(word_address, word_address + 1),
                If(inner_reader.sink.last,
                    NextState("WAIT_READ")
                )
            ),
            # Pipe out responses
            If(source.valid & source.ready,
                NextValue(reading_byte, reading_byte + 1),
                NextValue(reading_byte_local_address, reading_byte_local_address + 1)
            )
        )

        # Fetch remaining responses
        fsm.act("WAIT_READ",
            source.last.eq(reading_byte == sink.len),
            If(source.valid & source.ready,
                NextValue(reading_byte, reading_byte + 1),
                NextValue(reading_byte_local_address, reading_byte_local_address + 1),
                If(source.last,
                    sink.ready.eq(1), # Consume incoming
                    NextState("IDLE")
                )
            )
        )

        self.comb += [
            source.valid.eq(inner_reader.source.valid),
            inner_reader.source.ready.eq(source.ready &
                                       ((reading_byte_local_address == ratio - 1) | source.last)),
            Case(reading_byte_local_address, {
                Constant(i, ratio_bits):
                    source.data.eq(inner_reader.source.data[dw*i:dw*(i+1)])
                for i in range(ratio)
            })
        ]

# Warning: Writing has to start at a 128bit-aligned (16 bytes) address
class LiteEthIBMemoryRegionWriter(LiteXModule):
    """Infiniband Memory Region Writer

    A reader that accepts a virtual address and length and sends back the corresponding data from ram.
    Virtual address 0 corresponds to start of memory region.

    Parameters
    ----------
    region_start : int
        Physical address of the start
        of the memory region in the ram.
    region_size : int
        The size of the memory region.
    port : LiteDRAMNativePort
        Write port to physical sram.
    buffered_in : bool
        Whether to buffer input to cut timing
    buffered_out : bool
        Whether to buffer output to cut timing
    dw : int
        Width of the data bus.

    Attributes
    ----------
    sink : in
        Address and data to be written.
    source : out
        Error bit if invalid.
    """
    def __init__(self, region_start, region_size, port, buffered_in=True, dw=8):
        self.sink   = sink   = Endpoint([("data", dw), ("va", 64)])

        # # #

        if buffered_in:
            buff_in = Buffer([("data", dw), ("va", 64)])
            self.submodules += buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        # Parameters
        if (isinstance(port, LiteDRAMNativePort) or isinstance(port, LiteDRAMAXIPort)):
            port_width = port.data_width
        elif isinstance(port, _MemoryPort):
            port_width = len(port.dat_r)
        ratio      = port_width // dw
        ratio_bits = log2_int(ratio)

        assert dw == 8
        assert port_width % dw == 0

        # Submodules
        # Internal DMA writer
        # WARNING: I modified LiteDRAMDMAWriter to accept a last signal
        if (isinstance(port, LiteDRAMNativePort) or isinstance(port, LiteDRAMAXIPort)):
            inner_writer = LiteDRAMDMAWriter(port, fifo_depth=PMTU)
        elif isinstance(port, _MemoryPort):
            inner_writer = LiteBRAMWriter(port)
        fifo = SyncFIFO([("data", port_width),
                         ("address", 64 - ratio_bits)], 2, buffered=True)
        self.submodules += inner_writer, fifo

        # Signals
        # Current address we are writing to
        write_address = Signal(64)
        buffer        = Signal(port_width)
        buffer_next   = Signal().like(buffer)
        mask          = Signal(port_width)

        # FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid,
                NextValue(write_address, region_start + sink.va),
                NextValue(buffer, 0),
                NextState("WRITING")
            )
        )

        # Pipe in writes
        fsm.act("WRITING",
            fifo.sink.valid.eq(sink.valid & ((write_address[:ratio_bits] == ratio - 1) | sink.last)),
            sink.ready.eq(((write_address[:ratio_bits] != ratio - 1) & (~sink.last)) | fifo.sink.ready),
            If(sink.valid & sink.ready,
                NextValue(buffer, buffer_next),
                NextValue(write_address, write_address + 1),
                If(sink.last,
                    NextState("FINISHING")
                )
            )
        )

        fsm.act("FINISHING",
            If(fifo.source.valid & fifo.source.ready & fifo.source.last,
                NextState("IDLE")
            )
        )

        # Buffer logic
        buffer_case = {}
        mask_case = {}
        for i in range(ratio):
            buffer_case[i] = [buffer_next.eq(buffer),
                              buffer_next[dw*i:dw*(i+1)].eq(sink.data)]
            mask_case[i] = mask.eq(Replicate(0xff, i + 1))

        # Commands piped into fifo
        self.comb += [
            Case(write_address[:ratio_bits], buffer_case),
            Case(write_address[:ratio_bits], mask_case),
            fifo.sink.last.eq(sink.last),
            fifo.sink.data.eq(buffer_next),
            fifo.sink.address.eq(write_address[ratio_bits:]),
        ]

        # Commands piped from fifo to inner_writer
        self.comb += fifo.source.connect(inner_writer.sink)

class PERM(Flag):
    REMOTE_READ = auto()
    REMOTE_WRITE = auto()

class LiteEthIBMemoryRegion(LiteXModule):
    def __init__(self, region_start, region_size, permissions, r_key, read_port=None, write_port=None, dw=8):
        self.region_start = region_start
        self.region_size = region_size
        self.permissions = permissions
        self.r_key = r_key

        if (PERM.REMOTE_READ in permissions and read_port != None):
            self.reader = LiteEthIBMemoryRegionReader(region_start, region_size, read_port, dw)
        if (PERM.REMOTE_WRITE in permissions and write_port != None):
            self.writer = LiteEthIBMemoryRegionWriter(region_start, region_size, write_port, dw)


# A dictionary linking r_keys with memory regions
class LiteEthIBMemoryRegions(LiteXModule):
    def __init__(self):
        self._mrs = {}

    def get_mrs(self):
        return self._mrs.values()

    def reg_mr(self, region_start, region_size, permissions, r_key, read_port=None, write_port=None, dw=8):
        assert r_key >= 0 and r_key < 2**32
        assert r_key and r_key not in self._mrs.keys()

        self._mrs[r_key] = LiteEthIBMemoryRegion(region_start, region_size, permissions, r_key, read_port, write_port, dw)
        print(f"Added mr with r_key {r_key}")

        return self._mrs[r_key]

    def do_finalize(self):
        mrs = self.get_mrs()
        for i, memr in enumerate(mrs):
            self.add_module(f"memory_region{i}", memr)

