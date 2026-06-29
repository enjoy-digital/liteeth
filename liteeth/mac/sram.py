#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# Copyright (c) 2017 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

import math

from litex.gen import *

from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

from liteeth.common import *
from liteeth.mac.packet import LiteEthMACPacketWriter, LiteEthMACPacketReader

# MAC SRAM Writer ----------------------------------------------------------------------------------

class LiteEthMACSRAMWriter(LiteXModule):
    def __init__(self, dw, depth, nslots=2, endianness="big", timestamp=None, eth_mtu=eth_mtu_default):
        # Endpoint / Signals.
        self.crc_error = Signal()

        # Parameters Check / Compute.
        assert dw in [8, 16, 32, 64]
        self.eth_mtu = eth_mtu
        slotbits   = max(int(math.log2(nslots)), 1)
        lengthbits = bits_for(depth * dw//8)

        # CSRs.
        self._slot   = CSRStatus(slotbits,   description="Receive slot.")
        self._length = CSRStatus(lengthbits, description="Receive packet length in bytes.")
        self._errors = CSRStatus(32,         description="Receive packet drop count.")

        # Optional Timestamp of the incoming packets and expose value to software.
        if timestamp is not None:
            timestampbits   = len(timestamp)
            self._timestamp = CSRStatus(timestampbits, description="Receive packet timestamp.")

        # Event Manager.
        self.ev           = EventManager()
        self.ev.available = EventSourceLevel()
        self.ev.finalize()

        # # #

        # Packet frontend.
        self.packet = packet = LiteEthMACPacketWriter(dw, depth, eth_mtu=eth_mtu, timestamp=timestamp)
        self.sink = packet.sink
        self.comb += packet.source.ready.eq(1),

        errors = self._errors.status
        slot   = Signal(slotbits)

        # Status FIFO.
        stat_fifo_layout = [("slot", slotbits), ("length", lengthbits)]
        if timestamp is not None:
            stat_fifo_layout += [("timestamp", timestampbits)]
        self.stat_fifo = stat_fifo = stream.SyncFIFO(stat_fifo_layout, nslots)

        self.comb += [
            packet.enable.eq(stat_fifo.sink.ready),
            stat_fifo.sink.valid.eq(packet.done),
            stat_fifo.sink.slot.eq(slot),
            stat_fifo.sink.length.eq(packet.length),
            stat_fifo.source.ready.eq(self.ev.available.clear),
            self.ev.available.trigger.eq(stat_fifo.source.valid),
            self._slot.status.eq(stat_fifo.source.slot),
            self._length.status.eq(stat_fifo.source.length),
        ]
        if timestamp is not None:
            self.comb += self._timestamp.status.eq(stat_fifo.source.timestamp)
            self.comb += stat_fifo.sink.timestamp.eq(packet.timestamp)

        self.sync += [
            If(packet.drop, errors.eq(errors + 1)),
            If(packet.done & stat_fifo.sink.ready, slot.eq(slot + 1)),
        ]

        # Memory.
        wr_slot = slot
        wr_addr = packet.offset[int(math.log2(dw//8)):]
        wr_data = Signal(len(packet.source.data))

        # Create a Memory per Slot.
        mems  = [None] * nslots
        ports = [None] * nslots
        for n in range(nslots):
            mems[n]  = Memory(dw, depth, name=f"mac_sram_writer_slot{n}")
            ports[n] = mems[n].get_port(write_capable=True)
            self.specials += ports[n]
        self.mems = mems

        # Endianness Handling.
        self.comb += wr_data.eq({"big": reverse_bytes(packet.source.data), "little": packet.source.data}[endianness])

        # Connect Memory ports.
        cases = {}
        for n, port in enumerate(ports):
            self.comb += [
                port.adr.eq(wr_addr),
                port.dat_w.eq(wr_data),
            ]
            cases[n] = [port.we.eq(1)]

        self.comb += If(packet.source.valid & packet.source.ready, Case(wr_slot, cases))

# MAC SRAM Reader ----------------------------------------------------------------------------------

class LiteEthMACSRAMReader(LiteXModule):
    def __init__(self, dw, depth, nslots=2, endianness="big", timestamp=None):
        # Parameters Check / Compute.
        assert dw in [8, 16, 32, 64]
        slotbits   = max(int(math.log2(nslots)), 1)
        lengthbits = bits_for(depth * dw//8)

        # CSRs.
        self._start  = CSR()
        self._ready  = CSRStatus(description="Transmit command FIFO ready.")
        self._level  = CSRStatus(int(math.log2(nslots)) + 1, description="Transmit command FIFO level.")
        self._slot   = CSRStorage(slotbits,   reset_less=True, description="Transmit slot.")
        self._length = CSRStorage(lengthbits, reset_less=True, description="Transmit packet length in bytes.")

        # Optional Timestamp of the outgoing packets and expose value to software.
        if timestamp is not None:
            timestampbits        = len(timestamp)
            self._timestamp_slot = CSRStatus(slotbits,      description="Transmit timestamp slot.")
            self._timestamp      = CSRStatus(timestampbits, description="Transmit packet timestamp.")

        # Event Manager.
        self.ev      = EventManager()
        self.ev.done = EventSourcePulse() if timestamp is None else EventSourceLevel()
        self.ev.finalize()

        # # #

        # Packet frontend.
        self.packet = packet = LiteEthMACPacketReader(dw, depth, timestamp=timestamp)
        self.source = source = packet.source

        # Command FIFO.
        cmd_fifo = stream.SyncFIFO([("slot", slotbits), ("length", lengthbits)], nslots)
        self.submodules += cmd_fifo
        self.comb += [
            cmd_fifo.sink.valid.eq(self._start.wr_stb),
            cmd_fifo.sink.slot.eq(self._slot.storage),
            cmd_fifo.sink.length.eq(self._length.storage),
            self._ready.status.eq(cmd_fifo.sink.ready),
            self._level.status.eq(cmd_fifo.level)
        ]

        # Status FIFO (Only added when Timestamping).
        if timestamp is not None:
            stat_fifo_layout = [("slot", slotbits), ("timestamp", timestampbits)]
            stat_fifo = stream.SyncFIFO(stat_fifo_layout, nslots)
            self.submodules += stat_fifo
            self.comb += stat_fifo.source.ready.eq(self.ev.done.clear)
            self.comb += self._timestamp_slot.status.eq(stat_fifo.source.slot)
            self.comb += self._timestamp.status.eq(stat_fifo.source.timestamp)

        self.comb += packet.length.eq(cmd_fifo.source.length)

        # Memory.
        read      = Signal()
        rd_slot   = cmd_fifo.source.slot
        rd_offset = Signal(lengthbits)
        rd_data   = Signal(len(source.data))


        # FSM.
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(cmd_fifo.source.valid & packet.idle,
                packet.enable.eq(1),
                read.eq(1),
                NextValue(rd_offset, dw//8),
                NextState("READ")
            )
        )
        fsm.act("READ",
            packet.sink.valid.eq(1),
            packet.sink.last.eq(rd_offset >= cmd_fifo.source.length),
            If(packet.done,
                NextState("TERMINATE")
            ).Elif(packet.sink.ready & ~packet.sink.last,
                read.eq(1),
                NextValue(rd_offset, rd_offset + dw//8),
            )
        )
        fsm.act("TERMINATE",
            NextValue(rd_offset, 0),
            self.ev.done.trigger.eq(1),
            cmd_fifo.source.ready.eq(1),
            NextState("IDLE")
        )

        if timestamp is not None:
            self.comb += stat_fifo.sink.valid.eq(fsm.ongoing("TERMINATE"))
            self.comb += stat_fifo.sink.timestamp.eq(packet.timestamp)
            self.comb += stat_fifo.sink.slot.eq(cmd_fifo.source.slot)
            # Trigger event when Status FIFO has contents (Override FSM assignment).
            self.comb += self.ev.done.trigger.eq(stat_fifo.source.valid)

        # Create a Memory per Slot.
        mems    = [None]*nslots
        ports   = [None]*nslots
        for n in range(nslots):
            mems[n]  = Memory(dw, depth, name=f"mac_sram_reader_slot{n}")
            ports[n] = mems[n].get_port(has_re=True, mode=READ_FIRST)
            self.specials += ports[n]
        self.mems = mems

        # Connect Memory ports.
        cases = {}
        for n, port in enumerate(ports):
            self.comb += [
                port.re.eq(read),
                port.adr.eq(rd_offset[int(math.log2(dw//8)):])
            ]
            cases[n] = [rd_data.eq(port.dat_r)]

        self.comb += Case(rd_slot, cases)

        # Endianness Handling.
        self.comb += packet.sink.data.eq({"big" : reverse_bytes(rd_data), "little": rd_data}[endianness])

# MAC SRAM -----------------------------------------------------------------------------------------

class LiteEthMACSRAM(LiteXModule):
    def __init__(self, dw, depth, nrxslots, ntxslots, endianness, timestamp=None, eth_mtu=eth_mtu_default):
        self.writer = LiteEthMACSRAMWriter(dw, depth, nrxslots, endianness, timestamp, eth_mtu=eth_mtu)
        self.reader = LiteEthMACSRAMReader(dw, depth, ntxslots, endianness, timestamp)
        self.ev     = SharedIRQ(self.writer.ev, self.reader.ev)
        self.sink, self.source = self.writer.sink, self.reader.source
