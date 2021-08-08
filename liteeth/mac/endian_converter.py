#
# This file is part of LiteEth.
#
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

class LiteEthMACEndianConverter(Module):
    def __init__(self, dw):
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))
        self.source = source = stream.Endpoint(eth_phy_description(dw))
        self.comb += [
            sink.connect(source),
            source.data.eq(reverse_bytes(sink.data)),
            source.last_be.eq(reverse_bits(sink.last_be)),
            source.error.eq(reverse_bits(sink.error)),
        ]
