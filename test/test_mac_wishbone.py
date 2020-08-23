#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_sim import *

from liteeth.common import *
from liteeth.mac import LiteEthMAC

from test.model import phy, mac

from litex.gen.sim import *


class WishboneMaster:
    def __init__(self, obj):
        self.obj = obj
        self.dat = 0

    def write(self, adr, dat):
        yield self.obj.cyc.eq(1)
        yield self.obj.stb.eq(1)
        yield self.obj.adr.eq(adr)
        yield self.obj.we.eq(1)
        yield self.obj.sel.eq(0xf)
        yield self.obj.dat_w.eq(dat)
        while not (yield self.obj.ack):
            yield
        yield self.obj.cyc.eq(0)
        yield self.obj.stb.eq(0)
        yield

    def read(self, adr):
        yield self.obj.cyc.eq(1)
        yield self.obj.stb.eq(1)
        yield self.obj.adr.eq(adr)
        yield self.obj.we.eq(0)
        yield self.obj.sel.eq(0xf)
        yield self.obj.dat_w.eq(0)
        while not (yield self.obj.ack):
            yield
        self.dat = (yield self.obj.dat_r)
        yield self.obj.cyc.eq(0)
        yield self.obj.stb.eq(0)
        yield


class SRAMReaderDriver:
    def __init__(self, obj):
        self.obj = obj

    def start(self, slot, length):
        yield self.obj._slot.storage.eq(slot)
        yield self.obj._length.storage.eq(length)
        yield self.obj._start.re.eq(1)
        yield
        yield self.obj._start.re.eq(0)
        yield

    def wait_done(self):
        while not (yield self.obj.ev.done.pending):
            yield

    def clear_done(self):
        yield self.obj.ev.pending.re.eq(1)
        yield self.obj.ev.pending.r.eq(1)
        yield
        yield self.obj.ev.pending.re.eq(0)
        yield self.obj.ev.pending.r.eq(0)
        yield


class SRAMWriterDriver:
    def __init__(self, obj):
        self.obj = obj

    def wait_available(self):
        while not (yield self.obj.ev.available.pending):
            yield

    def clear_available(self):
        yield self.obj.ev.pending.re.eq(1)
        yield self.obj.ev.pending.r.eq(1)
        yield
        yield self.obj.ev.pending.re.eq(0)
        yield self.obj.ev.pending.r.eq(0)
        yield


class DUT(Module):
    def __init__(self):
        self.submodules.phy_model = phy.PHY(8, debug=False)
        self.submodules.mac_model = mac.MAC(self.phy_model, debug=False, loopback=True)
        self.submodules.ethmac = LiteEthMAC(phy=self.phy_model, dw=32, interface="wishbone", with_preamble_crc=True)


def main_generator(dut):
    wishbone_master = WishboneMaster(dut.ethmac.bus)
    sram_reader_driver = SRAMReaderDriver(dut.ethmac.interface.sram.reader)
    sram_writer_driver = SRAMWriterDriver(dut.ethmac.interface.sram.writer)

    sram_writer_slots_offset = [0x000, 0x200]
    sram_reader_slots_offset = [0x400, 0x600]

    length = 150+2

    tx_payload = [seed_to_data(i, True) % 0xff for i in range(length)] + [0, 0, 0, 0]

    errors = 0

    for i in range(2):
        for slot in range(2):
            print("slot {}: ".format(slot), end="")
            # fill tx memory
            for i in range(length//4+1):
                dat = int.from_bytes(tx_payload[4*i:4*(i+1)], "big")
                yield from wishbone_master.write(sram_reader_slots_offset[slot]+i, dat)

            # send tx payload & wait
            yield from sram_reader_driver.start(slot, length)
            yield from sram_reader_driver.wait_done()
            yield from sram_reader_driver.clear_done()

            # wait rx
            yield from sram_writer_driver.wait_available()
            yield from sram_writer_driver.clear_available()

            # get rx payload (loopback on PHY Model)
            rx_payload = []
            for i in range(length//4+1):
                yield from wishbone_master.read(sram_writer_slots_offset[slot]+i)
                dat = wishbone_master.dat
                rx_payload += list(dat.to_bytes(4, byteorder='big'))

            # check results
            s, l, e = check(tx_payload[:length], rx_payload[:min(length, len(rx_payload))])
            print("shift " + str(s) + " / length " + str(l) + " / errors " + str(e))


class TestMACWishbone(unittest.TestCase):
    def test(self):
        dut = DUT()
        generators = {
            "sys" :    main_generator(dut),
            "eth_tx": [dut.phy_model.phy_sink.generator(),
                       dut.phy_model.generator()],
            "eth_rx":  dut.phy_model.phy_source.generator()
        }
        clocks = {"sys":    20,
                  "eth_rx": 8,
                  "eth_tx": 8}
        run_simulation(dut, generators, clocks, vcd_name="sim.vcd")
