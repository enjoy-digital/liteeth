#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# Copyright (c) 2023 LumiGuide Fietsdetectie B.V. <goemansrowan@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

# MAC Gap ------------------------------------------------------------------------------------------

class LiteEthMACGap(Module):
    def __init__(self, dw, gap=None):
        self.sink   = sink   = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # # #
        counter_bits, _ = value_bits_sign(gap)
        counter = Signal(max=2**counter_bits, reset_less=True)

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            NextValue(counter, gap),
            sink.connect(source),
            If(sink.valid & sink.last & sink.ready,
                NextState("GAP")
            )
        )
        fsm.act("GAP",
            NextValue(counter, counter - 1),
            If(counter == 1,
                NextState("COPY")
            )
        )
