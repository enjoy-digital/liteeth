import unittest
import os
from litex.gen import *

import liteeth.core.rocev2.mr as mr

DEBUG = 0

class DUT(LiteXModule):
    def __init__(self, write, read, mem_num, region_size, dw):
        self.mrs_handler = mr.LiteEthIBMemoryRegionsHandler()

        if write:
            # Memory regions used for RDMA WRITE requests
            wmem = mr.LiteEthDummyTXMR(4096, dw)
            self.wmem = wmem = ClockDomainsRenamer({
                "sys" : "sys",
                "read"  : "sys",
                "write": "sys"
            })(wmem)
            self.wmem_mrs = []
            for i in range(mem_num // 2):
                wmem_port = wmem.get_read_port()
                wmem_mr = self.mrs_handler.reg_mr(
                    region_start = 0,
                    region_size  = region_size,
                    permissions  = mr.PERM(0),
                    r_key        = i,
                    l_key        = i,
                    read_port    = wmem_port,
                    write_port   = None
                )

                self.wmem_mrs.append(wmem_mr)

        if read:
            # Memory regions used for RDMA READ requests
            rmem = mr.LiteEthDummyRXMR(4096, dw)
            self.rmem = rmem = ClockDomainsRenamer({
                "sys": "sys",
                "write": "sys",
                "read": "sys"
            })(rmem)
            self.rmem_mrs = []
            for i in range(mem_num // 2):
                rmem_port = rmem.get_write_port()
                rmem_mr = self.mrs_handler.reg_mr(
                    region_start = 0,
                    region_size  = region_size,
                    permissions  = mr.PERM.LOCAL_WRITE | mr.PERM.NO_LOCAL_READ,
                    r_key        = mem_num // 2 + i,
                    l_key        = mem_num // 2 + i,
                    read_port    = None,
                    write_port   = rmem_port
                )

                self.rmem_mrs.append(rmem_mr)

PMTU = 1024

# Testing RX
def write_to_mem(writer, va, data):
    i = 0
    while not ((yield writer.sink.valid) and (yield writer.sink.ready) and (yield writer.sink.last)):
        if (yield writer.sink.valid) and (yield writer.sink.ready):
            i += 1
        yield writer.sink.data.eq(data[va + i])
        yield writer.sink.va.eq(va + i)
        yield writer.sink.valid.eq(1)
        if DEBUG:
            print(f"> Send\t0x{data[i]:02x} from {i}")
        yield writer.sink.last.eq(i == PMTU - 1)
        yield

    assert i == PMTU - 1
    yield writer.sink.valid.eq(0)

def send_read(dut, data):
    # Skip reset on init
    yield

    for i, memr in enumerate(dut.rmem_mrs):
        print(f">>> Writing to memory region {i}")
        writer = memr.writer

        yield from write_to_mem(writer, 2*PMTU*i, data)

        # Pause
        for _ in range(5):
            yield

        yield from write_to_mem(writer, 2*PMTU*i + PMTU, data)

        # Pause
        for _ in range(5):
            yield

        print(f"<<< Done writing to memory region {i}")

def receive_read(dut, data):
    # Skip reset on init
    yield

    read_port = dut.rmem.read_port

    yield read_port.source.ready.eq(1)

    i = 0
    while i < len(data):
        if (yield read_port.source.valid) and (yield read_port.source.ready):
            data[i] = (yield read_port.source.data)
            if DEBUG:
                print(f"< Rec\t0x{data[i]:02x} at {i}")
            i += 1
        yield

    yield read_port.source.ready.eq(0)

# Testing TX
def send_write(dut, data):
    # Skip reset on init
    yield

    write_port = dut.wmem.write_port

    yield write_port.sink.valid.eq(1)

    yield write_port.sink.data.eq(data[0])
    i = 0
    while True:
        if (yield write_port.sink.valid) and (yield write_port.sink.ready):
            if DEBUG:
                print(f"> Send\t0x{data[i]:02x} from {i}")
            i += 1
            if i == 4 * PMTU:
                break
            yield write_port.sink.data.eq(data[i])
        yield

    yield write_port.sink.valid.eq(0)
    yield

def read_from_mem(reader, va, data):
    # Skip reset on init
    yield

    yield reader.sink.valid.eq(1)
    yield reader.sink.va.eq(va)
    yield reader.sink.len.eq(PMTU)

    yield reader.source.ready.eq(1)

    i = 0
    while True:
        if (yield reader.sink.valid) and (yield reader.sink.ready):
            yield reader.sink.valid.eq(0)
        if (yield reader.source.valid):
            if DEBUG:
                print(f"< Rec\t0x{(yield reader.source.data):02x} at {va + i}")
            data[va + i] = (yield reader.source.data)
            i += 1
            if ((yield reader.source.ready) and (yield reader.source.last)):
                break
        yield

    assert i == PMTU
    yield reader.source.ready.eq(0)


def receive_write(dut, data):
    # Skip reset on init
    yield

    for i, memr in enumerate(dut.wmem_mrs):
        print(f">>> Reading from memory region {i}")

        reader = memr.reader

        yield from read_from_mem(reader, 2*PMTU*i, data)

        # Pause
        for _ in range(5):
            yield

        yield from read_from_mem(reader, 2*PMTU*i + PMTU, data)

        # Pause
        for _ in range(5):
            yield

        print(f"<<< Done reading from memory region {i}")

class TestMemoryDummies(unittest.TestCase):
    dw = 8
    def test_read(self):
        dut = DUT(
            write       = False,
            read        = True,
            mem_num     = 4,
            region_size = 2048,
            dw          = self.dw
        )

        sent_data = bytearray(os.urandom(4096))
        received_data = [0] * len(sent_data)
        run_simulation(dut, [
            send_read(dut, sent_data),
            receive_read(dut, received_data)
        ], vcd_name="test.vcd")

        for s, r in zip(sent_data, received_data):
            self.assertEqual(s, r)

    def test_write(self):
        dut = DUT(
            write       = True,
            read        = False,
            mem_num     = 4,
            region_size = 2048,
            dw          = self.dw
        )

        sent_data = bytearray(os.urandom(4096))
        received_data = [0] * len(sent_data)
        run_simulation(dut, [
            send_write(dut, sent_data),
            receive_write(dut, received_data)
        ], vcd_name="test.vcd")

        for s, r in zip(sent_data, received_data):
            self.assertEqual(s, r)

if __name__ == "__main__":
    unittest.main()
