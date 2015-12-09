from liteeth.common import *

from litex.gen.genlib.cdc import MultiReg
from litex.gen.fhdl.specials import Tristate


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
