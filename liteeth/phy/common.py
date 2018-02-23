from liteeth.common import *

from migen.genlib.cdc import MultiReg
from migen.fhdl.specials import Tristate

class LiteEthPHYHWReset(Module):
    def __init__(self):
        self.reset = Signal()

        # # #

        counter = Signal(max=512)
        counter_done = Signal()
        counter_ce = Signal()
        self.sync += If(counter_ce, counter.eq(counter + 1))
        self.comb += [
            counter_done.eq(counter == 256),
            counter_ce.eq(~counter_done),
            self.reset.eq(~counter_done)
        ]


class LiteEthPHYMDIO(Module, AutoCSR):
    def __init__(self, pads):
        self._w = CSRStorage(3, name="w")
        self._r = CSRStatus(1, name="r")

        # # #

        data_w = Signal()
        data_oe = Signal()
        data_r = Signal()
        self.comb +=[
            pads.mdc.eq(self._w.storage[0]),
            data_oe.eq(self._w.storage[1]),
            data_w.eq(self._w.storage[2])
        ]
        self.specials += [
            MultiReg(data_r, self._r.status[0]),
            Tristate(pads.mdio, data_w, data_oe, data_r)
        ]
