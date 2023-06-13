#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from litex.gen import *

from liteeth.common import *

from migen.genlib.cdc import MultiReg
from migen.fhdl.specials import Tristate

# LiteEth PHY HWReset ------------------------------------------------------------------------------

class LiteEthPHYHWReset(Module):
    def __init__(self, cycles=256):
        self.reset = Signal()

        # # #

        counter      = Signal(max=cycles + 1)
        counter_done = Signal()
        counter_ce   = Signal()
        self.sync += If(counter_ce, counter.eq(counter + 1))
        self.comb += [
            counter_done.eq(counter == cycles),
            counter_ce.eq(~counter_done),
            self.reset.eq(~counter_done)
        ]

# LiteEth PHY MDIO ---------------------------------------------------------------------------------

class LiteEthPHYMDIO(LiteXModule):
    def __init__(self, pads):
        self._w = CSRStorage(fields=[
            CSRField("mdc", size=1),
            CSRField("oe",  size=1),
            CSRField("w",   size=1)],
            name="w"
        )
        self._r = CSRStatus(fields=[
            CSRField("r", size=1)],
            name="r"
        )

        # # #

        data_w  = Signal()
        data_oe = Signal()
        data_r  = Signal()
        self.comb += [
            pads.mdc.eq(self._w.storage[0]),
            data_oe.eq( self._w.storage[1]),
            data_w.eq(  self._w.storage[2]),
        ]
        self.specials += MultiReg(data_r, self._r.status[0])
        self.specials += Tristate(pads.mdio, data_w, data_oe, data_r)
