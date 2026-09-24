from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import Buffer, Endpoint, SyncFIFO, AsyncFIFO

from litedram.frontend.dma import LiteDRAMDMAReader, LiteDRAMDMAWriter

from litedram.common import LiteDRAMNativePort
from litedram.frontend.axi import LiteDRAMAXIPort
from migen.fhdl.specials import _MemoryPort

from enum import Flag, auto

class LiteEthDummyMRReader(LiteXModule):
    """Dummy MR Reader

    Reads data out from Dummy MR (a stream).

    Parameters
    ----------
    port : ReadPort
        Read port to Dummy MR.

    Attributes
    ----------
    sink : in
        Address of data to be read.
        (address is only here for compatibility as the
        stream has no addresses)
    source : out
        Data being read.
    """
    def __init__(self, port):
        # The sink is a dummy, since there are no addresses in a FIFO
        self.sink   = sink   = Endpoint([("address", 1)])
        self.source = source = Endpoint([("data", port.data_width)])

        # # #

        self.buff_out = buff_out = Buffer([("data", port.data_width)])

        self.sync += [
            If(buff_out.sink.valid & buff_out.sink.ready & buff_out.sink.last,
                port.lock.eq(0)
            ).Elif(sink.valid,
                port.lock.eq(1)
            )
        ]
        self.comb += [
            port.source.ready.eq(buff_out.sink.ready & buff_out.sink.valid),
            sink.ready.eq(buff_out.sink.ready & buff_out.sink.valid),
            buff_out.sink.valid.eq(sink.valid & port.source.valid),
            buff_out.sink.data.eq(port.source.data),
            buff_out.sink.last.eq(sink.last),
            buff_out.source.connect(source)
        ]

class LiteEthDummyMRWriter(LiteXModule):
    """Dummy MR Writer

    Writes data into Dummy MR (a stream).

    Parameters
    ----------
    port : WritePort
        Write port to Dummy MR.

    Attributes
    ----------
    sink : in
        Address and data being written.
        (address is only here for compatibility as the
        stream has no addresses)
    """
    def __init__(self, port):
        self.sink = sink = Endpoint([("address", 1), ("data", port.data_width)])

        # # #

        self.buff_in = buff_in = Buffer([("data", port.data_width)])

        self.comb += [
            sink.connect(buff_in.sink, omit={"address"}),
            port.sink.data.eq(buff_in.source.data),
            port.sink.valid.eq(buff_in.source.valid),
            buff_in.source.ready.eq(port.sink.ready),
            port.sink.last.eq(buff_in.source.last),
        ]

class ReadPort:
    def __init__(self, dw):
        self.data_width = dw
        self.source = Endpoint([("data", dw)], name="read_port_source")
        self.lock = Signal()

class WritePort:
    def __init__(self, dw):
        self.data_width = dw
        self.sink = Endpoint([("data", dw)])

from litex.soc.interconnect.packet import Arbiter

class MyDispatcher(LiteXModule):
    def __init__(self, master, slaves, one_hot=False, discard=True, **kwargs):
        if len(slaves) == 0:
            self.sel = Signal()
        elif len(slaves) == 1 and not one_hot:
            self.comb += master.connect(slaves.pop(), **kwargs)
            self.sel = Signal()
        else:
            if one_hot:
                self.sel = Signal(len(slaves))
            else:
                self.sel = Signal(max=len(slaves))

            # # #

            sel = Signal.like(self.sel)
            self.comb += sel.eq(self.sel)
            # Hold the route from first beat to packet completion.
            cases = {}
            for i, slave in enumerate(slaves):
                if one_hot:
                    idx = 2**i
                else:
                    idx = i
                cases[idx] = [master.connect(slave, **kwargs)]
            cases["default"] = [master.ready.eq(discard)]
            self.comb += Case(sel, cases)

@ResetInserter(clock_domains=["sys"])
class LiteEthDummyRXMR(LiteXModule):
    def __init__(self, depth, dw):
        self.dw = dw
        self.fifo = fifo = AsyncFIFO(
            layout = [("data", dw)],
            depth  = depth
        )

        self.write_ports = []
        self.read_port = read_port = ReadPort(dw)

        self.comb += fifo.source.connect(read_port.source)

    def get_write_port(self):
        write_port = WritePort(self.dw)

        self.write_ports.append(write_port)
        return write_port

    def do_finalize(self):
        sinks = [write_port.sink for write_port in self.write_ports]
        self.arbiter = Arbiter(sinks, self.fifo.sink)

@ResetInserter(clock_domains=["sys"])
class LiteEthDummyTXMR(LiteXModule):
    def __init__(self, depth, dw):
        self.dw = dw
        self.fifo = fifo = AsyncFIFO(
            layout = [("data", dw)],
            depth  = depth
        )

        self.write_port = write_port = WritePort(dw)
        self.read_ports = []

        self.comb += write_port.sink.connect(fifo.sink)

    def get_read_port(self):
        read_port = ReadPort(self.dw)
        self.read_ports.append(read_port)
        return read_port

    def do_finalize(self):
        self.lock_fifo = lock_fifo = SyncFIFO([("lock", len(self.read_ports))], len(self.read_ports))

        locks = Signal(len(self.read_ports))
        locks_prev = Signal().like(locks, reset_less=True)
        locks_new = Signal().like(locks)
        unlocks_new = Signal().like(locks)

        self.comb += [
            locks.eq(Cat(*[read_port.lock for read_port in self.read_ports])),
            locks_new.eq(~locks_prev & locks),
            unlocks_new.eq(locks_prev & ~locks),
            lock_fifo.sink.valid.eq(locks_new != 0),
            Case(locks_new, {
                Constant(1 << i, len(locks)):
                lock_fifo.sink.lock.eq(locks_new)
                for i in range(len(self.read_ports))
            }),
        ]
        self.sync += locks_prev.eq(locks)

        # Normal dispatcher discards incoming data when no source is selected.
        # In our design, it needs to wait
        sources = [read_port.source for read_port in self.read_ports]
        self.dispatcher = dispatcher = MyDispatcher(self.fifo.source, sources, one_hot=True, discard=False)
        self.comb += [
            If(lock_fifo.source.valid,
                dispatcher.sel.eq(lock_fifo.source.lock),
            ),
            lock_fifo.source.ready.eq(unlocks_new != 0)
        ]

class LiteBRAMReader(LiteXModule):
    """BRAM Reader

    Reads data out from block RAM.

    Parameters
    ----------
    port : _MemoryPort
        Read port to BRAM.

    Attributes
    ----------
    sink : in
        Address of data to be read.
    source : out
        Data being read.
    """
    def __init__(self, port):
        self.sink   = sink = Endpoint([("address", len(port.adr))])
        self.source = source = Endpoint([("data", len(port.dat_r))])

        # # #

        # Bufferize the data being output with fifo
        out_fifo = SyncFIFO([("data", len(port.dat_r))], 32)
        self.submodules.out_fifo = out_fifo
        self.comb += port.adr.eq(sink.address)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid,
                NextState("READ")
            )
        )
        fsm.act("READ",
            out_fifo.sink.data.eq(port.dat_r),
            out_fifo.sink.last.eq(sink.last),
            out_fifo.sink.valid.eq(1),
            If(out_fifo.sink.ready,
                sink.ready.eq(1),
                NextState("IDLE")
            )
        )

        self.comb += out_fifo.source.connect(source)

class LiteBRAMWriter(LiteXModule):
    """BRAM Writer

    Writes data into block RAM.

    Parameters
    ----------
    port : _MemoryPort
        Write port to BRAM.

    Attributes
    ----------
    sink : in
        Address and data being written.
    """
    def __init__(self, port):
        self.sink   = sink = Endpoint([
            ("address", len(port.adr)),
            ("data", len(port.dat_w))
        ])

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
    port : LiteDRAMNativePort | LiteDRAMAXIPort | _MemoryPort
        Read port to physical ram.
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
    source : out
        Data being read.
    """
    def __init__(self, region_start, port, buffered_in=True, buffered_out=True, dw=8):
        self.sink   = sink   = Endpoint([("va", 64), ("len", bits_for(PMTU))])
        self.source = source = Endpoint([("data", dw)])

        # # #

        assert dw in [8, 16, 32, 64]
        assert isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort, _MemoryPort, ReadPort))

        # Bufferize sink to cut timing path
        if buffered_in:
            buff_in = Buffer([("va", 64), ("len", bits_for(PMTU))])
            self.submodules.buff_in = buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        if buffered_out:
            buff_out = Buffer([("data", dw)])
            self.submodules.buff_out = buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink


        if isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort, ReadPort)):
            port_width = port.data_width
        else: # isinstance(port, _MemoryPort)
            port_width = len(port.dat_r)
        assert port_width in [8, 16, 32, 64]

        # Internal DMA reader
        if isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort)):
            inner_reader = LiteDRAMDMAReader(port, fifo_depth=PMTU)
        elif isinstance(port, _MemoryPort):
            inner_reader = LiteBRAMReader(port)
        else: # isinstance(port, ReadPort)
            inner_reader = LiteEthDummyMRReader(port)
        self.inner_reader = inner_reader

        if port_width > dw:
            ratio          = port_width // dw
            ratio_bits     = log2_int(ratio)

            assert dw == 8

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

        elif port_width == dw:
            assert dw == 8
            # Address of word to be used for next read request
            word_address = Signal(64)
            # The number of the word being output from the DMA reader
            reading_byte = Signal(bits_for(PMTU))

            # Reader FSM
            self.fsm = fsm = FSM(reset_state="IDLE")
            fsm.act("IDLE",
                If(sink.valid,
                    NextValue(word_address, (region_start + sink.va)),
                    NextValue(reading_byte, 1),
                    NextState("READING")
                )
            )

            fsm.act("READING",
                # Pipe in requests
                inner_reader.sink.address.eq(word_address),
                inner_reader.sink.valid.eq(1),
                inner_reader.sink.last.eq(word_address == region_start + sink.va + sink.len - 1),
                If(inner_reader.sink.ready,
                    NextValue(word_address, word_address + 1),
                    If(inner_reader.sink.last,
                        NextState("WAIT_READ")
                    )
                ),
                # Pipe out responses
                If(source.valid & source.ready,
                    NextValue(reading_byte, reading_byte + 1)
                )
            )

            # Fetch remaining responses
            fsm.act("WAIT_READ",
                source.last.eq(reading_byte == sink.len),
                If(source.valid & source.ready,
                    NextValue(reading_byte, reading_byte + 1),
                    If(source.last,
                        sink.ready.eq(1), # Consume incoming
                        NextState("IDLE")
                    )
                )
            )

            self.comb += [
                source.valid.eq(inner_reader.source.valid),
                inner_reader.source.ready.eq(source.ready),
                source.data.eq(inner_reader.source.data)
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
    port : LiteDRAMNativePort | LiteDRAMAXIPort | _MemoryPort
        Write port to physical ram.
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
    """
    def __init__(self, region_start, port, buffered_in=True, dw=8):
        self.sink   = sink   = Endpoint([("data", dw), ("va", 64)])

        # # #

        assert dw in [8, 16, 32, 64]
        assert isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort, _MemoryPort, WritePort))

        # Adding buffer at memory region sink
        if buffered_in:
            buff_in = Buffer([("data", dw), ("va", 64)])
            self.submodules.buff_in = buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        # Parameters
        if isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort, WritePort)):
            port_width = port.data_width
        else: # isinstance(port, _MemoryPort)
            port_width = len(port.dat_w)

        assert port_width in [8, 16, 32, 64]

        # Submodules
        # Internal DMA writer
        if isinstance(port, (LiteDRAMNativePort, LiteDRAMAXIPort)):
            inner_writer = LiteDRAMDMAWriter(port, fifo_depth=PMTU)
        elif isinstance(port, _MemoryPort):
            inner_writer = LiteBRAMWriter(port)
        else: # isinstance(port, WritePort)
            inner_writer = LiteEthDummyMRWriter(port)
        self.submodules.inner_writer = inner_writer

        if port_width > dw:
            assert dw == 8

            ratio      = port_width // dw
            ratio_bits = log2_int(ratio)

            # Assemble command and data for a larger memory port width
            in_fifo = SyncFIFO(
                layout = [
                    ("data", port_width),
                    ("address", 64 - ratio_bits)
                ],
                depth    = 2,
                buffered = True
            )
            self.in_fifo = in_fifo

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
                in_fifo.sink.valid.eq(sink.valid & ((write_address[:ratio_bits] == ratio - 1) | sink.last)),
                sink.ready.eq(((write_address[:ratio_bits] != ratio - 1) & (~sink.last)) | in_fifo.sink.ready),
                If(sink.valid & sink.ready,
                    NextValue(buffer, buffer_next),
                    NextValue(write_address, write_address + 1),
                    If(sink.last,
                        NextState("FINISHING")
                    )
                )
            )

            fsm.act("FINISHING",
                If(in_fifo.source.valid & in_fifo.source.ready & in_fifo.source.last,
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
                in_fifo.sink.last.eq(sink.last),
                in_fifo.sink.data.eq(buffer_next),
                in_fifo.sink.address.eq(write_address[ratio_bits:]),
            ]

            # Commands piped from fifo to inner_writer
            self.comb += in_fifo.source.connect(inner_writer.sink)

        elif port_width == dw:
            assert dw == 8

            # Assemble command and data for a larger memory port width
            in_fifo = SyncFIFO(
                layout = [
                    ("data", port_width),
                    ("address", 64)
                ],
                depth    = 2,
                buffered = True
            )
            self.in_fifo = in_fifo

            # Signals
            # Current address we are writing to
            write_address = Signal(64)

            # FSM
            self.fsm = fsm = FSM(reset_state="IDLE")
            fsm.act("IDLE",
                If(sink.valid,
                    NextValue(write_address, region_start + sink.va),
                    NextState("WRITING")
                )
            )

            # Pipe in writes
            fsm.act("WRITING",
                in_fifo.sink.valid.eq(sink.valid),
                sink.ready.eq(in_fifo.sink.ready),
                If(sink.valid & sink.ready,
                    NextValue(write_address, write_address + 1),
                    If(sink.last,
                        NextState("FINISHING")
                    )
                )
            )

            fsm.act("FINISHING",
                If(in_fifo.source.valid & in_fifo.source.ready & in_fifo.source.last,
                    NextState("IDLE")
                )
            )

            # Commands piped into fifo
            self.comb += [
                in_fifo.sink.last.eq(sink.last),
                in_fifo.sink.data.eq(sink.data),
                in_fifo.sink.address.eq(write_address),
            ]

            # Commands piped from fifo to inner_writer
            self.comb += in_fifo.source.connect(inner_writer.sink)

class PERM(Flag):
    REMOTE_READ = auto()
    REMOTE_WRITE = auto()
    LOCAL_WRITE = auto()
    NO_LOCAL_READ = auto() # We cannot send from this memory

@ResetInserter()
class LiteEthIBMemoryRegion(LiteXModule):
    """Infiniband Memory Region

    Keep track of memory region information and instantiate a reader and writer,
    depending on permissions.

    Parameters
    ----------
    region_start : int
        Start of the memory region.
    region_size : int
        Size of the memory region.
    permissions : PERM
        Permissions for rdma to use the ram.
    l_key : int
        Local key that allows local operations
        to reference the region.
    r_key : int
        Remote key that allows remote operations
        to reference the region.
    memory: Memory
        A reference to the underlying physical
        memory block.
    read_port: LiteDRAMNativePort | LiteDRAMAXIPort | _MemoryPort
        A port for reading from the underlying
        physical memory block.
    write_port: LiteDRAMNativePort | LiteDRAMAXIPort | _MemoryPort
        A port for writing to the underlying
        physical memory block.
    dw: int
        Datapath width.
    """
    def __init__(
            self,
            region_start,
            region_size,
            permissions,
            l_key, r_key,
            read_port=None,
            write_port=None,
            dw=8
        ):
        self.region_start = region_start
        self.region_size = region_size
        self.permissions = permissions
        self.l_key = l_key
        self.r_key = r_key

        if PERM.NO_LOCAL_READ not in permissions or PERM.REMOTE_READ in permissions:
            if read_port is None:
                raise ValueError("Read port needed!")
            self.submodules.reader = LiteEthIBMemoryRegionReader(
                region_start = region_start,
                port         = read_port,
                dw           = dw
            )

        if PERM.REMOTE_WRITE in permissions or PERM.LOCAL_WRITE in permissions:
            if write_port is None:
                raise ValueError("Write port needed with current permissions!")
            self.submodules.writer = LiteEthIBMemoryRegionWriter(
                region_start = region_start,
                port         = write_port,
                dw           = dw
            )


# A dictionary linking r_keys with memory regions
class LiteEthIBMemoryRegionsHandler(LiteXModule):
    """Infiniband Memory Regions Handler

    Keep track of memory regions information and maintaining unique l_keys and r_keys.
    """
    def __init__(self):
        self._l_keys = set()
        self._r_keys = set()
        self.mrs = []

        self.reset = Signal(reset=1)
        self.sync += self.reset.eq(0)

    def reg_mr(self,
               region_start,
               region_size,
               permissions,
               l_key,
               r_key,
               read_port=None,
               write_port=None,
               dw=8
        ):
        # Ensure validity and uniqueness of l_key and r_key
        assert l_key is not None and l_key not in self._l_keys
        assert l_key >= 0 and l_key < 2**32

        assert r_key is not None and r_key not in self._r_keys
        assert r_key >= 0 and r_key < 2**32

        # Instantiate memory region
        mr = LiteEthIBMemoryRegion(
            region_start = region_start,
            region_size  = region_size,
            permissions  = permissions,
            l_key        = l_key,
            r_key        = r_key,
            read_port    = read_port,
            write_port   = write_port,
            dw           = dw
        )
        self.mrs.append(mr)
        self._l_keys.add(l_key)
        self._r_keys.add(r_key)
        print(f"Added mr with l_key {l_key} and r_key {r_key}")

        return mr

    def do_finalize(self):
        for i, memr in enumerate(self.mrs):
            self.sync += memr.reset.eq(self.reset)
            self.add_module(f"memory_region{i}", memr)
