#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# SPDX-License-Identifier: BSD-2-Clause

import math

from liteeth.common import *

# MAC Gap ------------------------------------------------------------------------------------------

class LiteEthMACGap(Module):
    def __init__(self, dw):
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #

        gap     = math.ceil(eth_interpacket_gap/(dw//8))
        counter = Signal(max=gap, reset_less=True)

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            NextValue(counter, 0),
            sink.connect(source),
            If(sink.valid & sink.last & sink.ready,
                NextState("GAP")
            )
        )
        fsm.act("GAP",
            NextValue(counter, counter + 1),
            If(counter == (gap-1),
                NextState("COPY")
            )
        )
