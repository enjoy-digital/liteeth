#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *

# MAC Gap ------------------------------------------------------------------------------------------

class LiteEthMACGap(Module):
    def __init__(self, dw, cycles=None):
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        if cycles is None:
            cycles = math.ceil(eth_interpacket_gap/(dw//8))
        counter = Signal(max=2**len(cycles), reset_less=True) if isinstance(cycles, Signal) else \
                  Signal(max=cycles + 1, reset_less=True)

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            sink.connect(source),
            If(sink.valid & sink.last & sink.ready,
                NextValue(counter, cycles),
                NextState("GAP")
            )
        )
        fsm.act("GAP",
            NextValue(counter, counter - 1),
            If(counter == 1,
                NextState("COPY")
            )
        )
