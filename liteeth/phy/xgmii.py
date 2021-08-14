#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
#
# SPDX-License-Identifier: BSD-2-Clause


from migen import Module
from liteeth.common import *

from functools import reduce
from operator import or_

XGMII_IDLE = Constant(0x07, bits_sign=8)
XGMII_START = Constant(0xFB, bits_sign=8)
XGMII_END = Constant(0xFD, bits_sign=8)

class LiteEthPHYXGMIITX(Module):
    def __init__(self, pads, dw):
        # Enforce 64-bit data path
        assert dw == 64

        # Sink for data to transmit
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))

        # Transmit FSM
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            If(sink.valid,
                # Currently idling, but a new frame is ready for
                # transmission. Thus transmit the preamble, but replace the
                # first byte with the XGMII start of frame control
                # character. Accept more data.
                pads.tx_ctl.eq(0x01),
                pads.tx_data.eq(Cat(XGMII_START, sink.data[8:dw])),
                NextValue(sink.ready, 1),
                NextState("TRANSMIT"),
            ).Else(
                # Idling, transmit XGMII IDLE control characters
                # only. Accept more data.
                pads.tx_ctl.eq(0xFF),
                pads.tx_data.eq(Cat(*([XGMII_IDLE] * 8))),
                NextValue(sink.ready, 1),
                NextState("IDLE"),
            )
        )

        fsm.act("TRANSMIT",
            # Check whether the data is still valid first or we are are not
            # ready to accept a new transmission.
            If(~sink.valid | ~sink.ready,
                # Data isn't valid, or we aren't ready to accept a new
                # transmission yet as another one has ended but the XGMII end of
                # frame control character has not been transmitted. We must
                # transmit the end of frame marker and return to
                # afterwards. Immediately accept more data, given we have
                # transmitted the end of frame control character.
                pads.tx_ctl.eq(0xFF),
                pads.tx_data.eq(Cat(XGMII_END, Replicate(XGMII_IDLE, 7))),
                NextValue(sink.ready, 1),
                NextState("IDLE"),
            ).Else(
                # The data is valid. For each byte, determine whether it is
                # valid or must be an XGMII idle or end of frame control
                # character based on the value of last_be.
                *[
                    If(~sink.last | (sink.last_be >= (1 << i)),
                        # Either not the last data word or last_be indicates
                        # this byte is still valid
                        pads.tx_ctl[i].eq(0),
                        pads.tx_data[8*i:8*(i+1)].eq(sink.data[8*i:8*(i+1)]),
                    ).Elif((sink.last_be == (1 << (i - 1))) if i > 0 else 0,
                        # last_be indicates that this byte is the first one
                        # which is no longer valid, hence transmit the XGMII end
                        # of frame character
                        pads.tx_ctl[i].eq(1),
                        pads.tx_data[8*i:8*(i+1)].eq(XGMII_END),
                    ).Else(
                        # We must've transmitted the XGMII end of frame control
                        # character, all other bytes must be XGMII idle control
                        # character
                        pads.tx_ctl[i].eq(1),
                        pads.tx_data[8*i:8*(i+1)].eq(XGMII_IDLE),
                    )
                    for i in range(8)
                ],
                # If this was the last data word, we must determine whether we
                # have transmitted the XGMII end of frame control character. The
                # only way this cannot happen is if every byte in the data word
                # was valid. If this is the case, we must send an additional
                # XGMII bus word containing the XGMII end of frame and idle
                # control characters. This happens if we remain in the TRANSMIT
                # state.
                If(~sink.last,
                    NextValue(sink.ready, 1),
                    NextState("TRANSMIT"),
                ).Elif(sink.last_be == (1 << 7),
                    # Last data word, but all bytes were valid.
                    NextValue(sink.ready, 0),
                    NextState("TRANSMIT"),
                ).Else(
                    NextValue(sink.ready, 1),
                    NextState("IDLE"),
                )
            )
        )

class LiteEthPHYXGMIIRXAligner(Module):
    def __init__(self, unaligned_ctl, unaligned_data):
        # Aligned ctl and data characters
        self.aligned_ctl = Signal.like(unaligned_ctl)
        self.aligned_data = Signal.like(unaligned_data)

        # Buffer for low-bytes of the last XGMII bus word
        low_ctl = Signal(len(unaligned_ctl) // 2)
        low_data = Signal(len(unaligned_data) // 2)


        # Alignment FSM
        self.submodules.fsm = fsm = FSM(reset_state="NOSHIFT")

        fsm.act("NOSHIFT",
            If(unaligned_ctl[4] & (unaligned_data[4*8:5*8] == XGMII_START),
                # Report this bus word as entirely idle. This should
                # not abort any existing transaction because of the
                # 5-byte interpacket gap.
                self.aligned_ctl.eq(0xFF),
                self.aligned_data.eq(Replicate(XGMII_IDLE, 8)),
                NextValue(low_ctl, unaligned_ctl[4:8]),
                NextValue(low_data, unaligned_data[4*8:8*8]),
                NextState("SHIFT"),
            ).Else(
                # Data is aligned on the first octet of the XGMII bus
                # word.
                self.aligned_ctl.eq(unaligned_ctl),
                self.aligned_data.eq(unaligned_data),
            ),
        )

        fsm.act("SHIFT",
            If(unaligned_ctl[0] & (unaligned_data[0*8:1*8] == XGMII_START),
                # Discard the previously recorded low bits,
                # immediately transmit the full bus word.
                self.aligned_ctl.eq(unaligned_ctl),
                self.aligned_data.eq(unaligned_data),
                NextState("NOSHIFT"),
            ).Else(
                # Data is aligned on the fifth octet of the XGMII bus
                # word. Store the low 4 octects and output the
                # previous ones.
                self.aligned_ctl.eq(Cat(low_ctl, unaligned_ctl[0:4])),
                self.aligned_data.eq(Cat(low_data, unaligned_data[0*8:4*8])),
                NextValue(low_ctl, unaligned_ctl[4:8]),
                NextValue(low_data, unaligned_data[4*8:8*8]),
            ),
        )

class LiteEthPHYXGMIIRX(Module):
    def __init__(self, pads, dw):
        # Enforce 64-bit data path
        assert dw == 64

        # Source we need to feed data into. We assume the sink is always ready,
        # given we can't really pause an incoming XGMII transfer.
        self.source = source = stream.Endpoint(eth_phy_description(dw))

        # As per IEEE802.3-2018, section eight, 126.3.2.2.10 Start, the XGMII
        # start control character is only valid on the first octet of a 32-bit
        # (DDR) XGMII bus word. This means that on a 64-bit XGMII bus word, a
        # new XGMII transmission may start on the first or firth
        # octect. However, this would require us to keep track of the current
        # offset and overall make this implementation more complex. Instead we
        # want all transmissions to be aligned on the first octect of a 64-bit
        # XGMII bus word, which we can do without packet loss given 10G Ethernet
        # mandates a 5-byte interpacket gap (which may be less at the receiver,
        # but this assumption seems to work for now).
        self.submodules.aligner = LiteEthPHYXGMIIRXAligner(pads.rx_ctl, pads.rx_data)

        # We need to have a lookahead and buffer the XGMII bus to properly
        # determine whether we are processing the last bus word in some
        # cases. This means delaying the incoming data by one cycle.
        xgmii_bus_layout = [ ("ctl", 8), ("data", 64) ]
        xgmii_bus_next = Record(xgmii_bus_layout)
        self.comb += [
            xgmii_bus_next.ctl.eq(self.aligner.aligned_ctl),
            xgmii_bus_next.data.eq(self.aligner.aligned_data),
        ]
        xgmii_bus = Record(xgmii_bus_layout)
        self.sync += [
            xgmii_bus.ctl.eq(xgmii_bus_next.ctl),
            xgmii_bus.data.eq(xgmii_bus_next.data),
        ]

        # Scan over the entire XGMII bus word and search for an XGMII_END
        # control character. If found, the octet before that must've been the
        # last valid byte.
        encoded_last_be = Signal(8)
        self.comb += [
            reduce(lambda a, b: a.Else(b), [
                If((xgmii_bus.ctl[i] == 1) & \
                   (xgmii_bus.data[i*8:(i+1)*8] == XGMII_END),
                    encoded_last_be.eq((1 << i - 1) if i > 0 else 0))
                for i in range(8)
            ]).Else(encoded_last_be.eq(1 << 7)),
        ]

        # If either the current XGMII bus word indicates an end of a XGMII bus
        # transfer (i.e. the encoded last_be is not 1 << 7, so the XGMII bus
        # word is only partially valid) OR the next bus word immediately
        # _starts_ with an XGMII end control character, the current bus data
        # must be last. Nonetheless, mask last by valid to avoid triggering
        # source.last on an empty XGMII bus word.
        xgmii_bus_next_immediate_end = Signal()
        self.comb += [
            xgmii_bus_next_immediate_end.eq(
                xgmii_bus_next.ctl[0] & (xgmii_bus_next.data[0:8] == XGMII_END)
            ),
            source.last.eq(
                source.valid & (
                    (encoded_last_be != (1 << 7)) | xgmii_bus_next_immediate_end
                ),
            ),
            If(source.last,
                source.last_be.eq(encoded_last_be),
            ).Else(
                source.last_be.eq(0),
            ),
        ]

        # Receive FSM
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            # The Ethernet preamble and start of frame character must follow
            # after the XGMII start control character, so we can simply match on
            # the entire bus word. The aligner makes sure the XGMII start
            # control character is always located on the first XGMII bus
            # word. This also spares us of looking for XGMII end of frame
            # characters, given we would need to immediately dismiss the
            # transmission if we find one of those.
            If((xgmii_bus.ctl == 0x01) & (xgmii_bus.data == Cat(
                    XGMII_START,
                    Constant(eth_preamble, bits_sign=64)[8:64],
               )),
                source.valid.eq(1),
                source.first.eq(1),
                source.data.eq(Constant(eth_preamble, bits_sign=64)),
                source.error.eq(0),
                If(source.last,
                   # It may happen that the lookahead concluded we're
                   # immediately ending the XGMII bus transfer. In this case,
                   # remain in IDLE
                   NextState("IDLE"),
                ).Else(
                   NextState("RECEIVE"),
                ),
            ).Else(
                # In any other case, keep the RX FSM idle. While there could be
                # a bus error condition, without a properly initiated Ethernet
                # transmission we couldn't meaningfully handle or report it
                # anyways.
                source.valid.eq(0),
                source.first.eq(0),
                source.data.eq(0),
                source.error.eq(0),
                NextState("IDLE"),
            ),
        )

        fsm.act("RECEIVE",
            # Receive data and pass through to the source. Switch back to IDLE
            # if we detect an XGMII end control character in this or at the
            # start of the next XGMII bus word.
            source.valid.eq(1),
            source.first.eq(0),
            source.data.eq(xgmii_bus.data),
            source.error.eq(0),
            If(source.last,
                NextState("IDLE"),
            ).Else(
                NextState("RECEIVE"),
            )
        )


class LiteEthPHYXGMIICRG(Module, AutoCSR):
    def __init__(self, clock_pads, model=False):
        self._reset = CSRStorage()
        self.clock_domains.cd_eth_rx = ClockDomain()
        self.clock_domains.cd_eth_tx = ClockDomain()
        if model:
            self.comb += [
                self.cd_eth_rx.clk.eq(ClockSignal()),
                self.cd_eth_tx.clk.eq(ClockSignal())
            ]
        else:
            self.comb += [
                self.cd_eth_rx.clk.eq(clock_pads.rx),
                self.cd_eth_tx.clk.eq(clock_pads.tx)
            ]

class LiteEthPHYXGMII(Module, AutoCSR):
    dw          = 8
    tx_clk_freq = 156.25e6
    rx_clk_freq = 156.25e6
    def __init__(self,
                 clock_pads,
                 pads,
                 model=False,
                 dw=64,
                 with_hw_init_reset=True):
        self.dw = dw
        self.cd_eth_tx, self.cd_eth_rx = "eth_tx", "eth_rx"
        self.submodules.crg = LiteEthPHYXGMIICRG(clock_pads, model)
        self.submodules.tx = ClockDomainsRenamer(self.cd_eth_tx)(
            LiteEthPHYXGMIITX(pads, self.dw))
        self.submodules.rx = ClockDomainsRenamer(self.cd_eth_rx)(
            LiteEthPHYXGMIIRX(pads, self.dw))
        self.sink, self.source = self.tx.sink, self.rx.source
