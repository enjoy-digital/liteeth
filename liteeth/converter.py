#
# This file is part of LiteEth.
#
# Copyright (c) 2015-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2018 whitequark <whitequark@whitequark.org>
# Copyright (c) 2021 David Sawatzke <d-git@sawatzke.dev>
# SPDX-License-Identifier: BSD-2-Clause

from liteeth.common import *

class LiteEthConverter(Module):
    def __init__(self, description_from, description_to, cd_from="sys", cd_to="sys"):
        self.sink   = sink = stream.Endpoint(description_from)
        self.source = source = stream.Endpoint(description_to)
        dw_from = [item for item in description_from.payload_layout if item[0] == "data"][0][1]
        dw_to = [item for item in description_to.payload_layout if item[0] == "data"][0][1]

        pipeline = []
        description = description_from

        if dw_from < dw_to:
            converter = stream.StrideConverter(
                description_from = description_from,
                description_to   = description_to)
            self.submodules += ClockDomainsRenamer(cd_from)(converter)
            pipeline += [converter]

            description = description_to

        # Becomes a no-op if cd_from==cd_to
        cdc = stream.ClockDomainCrossing(description, cd_from=cd_from, cd_to=cd_to)
        self.submodules += cdc
        pipeline += [cdc]

        if dw_from > dw_to:
            converter = stream.StrideConverter(
                description_from = description_from,
                description_to   = description_to)
            self.submodules += ClockDomainsRenamer(cd_to)(converter)
            pipeline += [converter]

            last_be_converter = LiteEthConverterLastBEDown(description_to)
            self.submodules += ClockDomainsRenamer(cd_to)(last_be_converter)
            pipeline += [last_be_converter]

        self.submodules.pipeline = stream.Pipeline(*pipeline)

        self.sink, self.source = self.pipeline.sink, self.pipeline.source

class LiteEthConverterBidir(Module):
    def __init__(self, description_a, description_b, cd_a="sys", cd_b="sys"):
        self.submodules.ab = LiteEthConverter(description_a, description_b, cd_a, cd_b)
        self.submodules.ba = LiteEthConverter(description_b, description_a, cd_b, cd_a)

class LiteEthConverterLastBEDown(Module):
    def __init__(self, description):
        self.sink   = sink = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="COPY")
        fsm.act("COPY",
            sink.connect(source),
            source.last.eq(sink.last_be != 0),
            If(sink.valid & sink.ready,
                # If last Byte but not last packet token.
                If(source.last & ~sink.last,
                    NextState("WAIT-LAST")
                )
            )
        )
        fsm.act("WAIT-LAST",
            # Accept incoming stream until we receive last packet token.
            sink.ready.eq(1),
            If(sink.valid & sink.last,
                NextState("COPY")
            )
        )

class LiteEthConverterLastBEUp(Module):
    def __init__(self, description):
        self.sink = sink = stream.Endpoint(description)
        self.source = source = stream.Endpoint(description)

        dw = [item for item in description.payload_layout if item[0] == "data"][0][1]
        # # #

        self.comb += [
            sink.connect(source),
            If(dw == 8,
                # 8bit streams might only drive last, thus `last_be` must be
                # controlled accordingly. Streams > 8bit must drive `last_be`
                # themselves.
                source.last_be.eq(sink.last)
            )
        ]
