#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
#
# SPDX-License-Identifier: BSD-2-Clause

from functools import reduce

from litex.gen import *

from liteeth.common import *

# Constants ----------------------------------------------------------------------------------------

XGMII_IDLE  = Constant(0x07, bits_sign=8)
XGMII_START = Constant(0xFB, bits_sign=8)
XGMII_END   = Constant(0xFD, bits_sign=8)

# Pads/Interfaces ----------------------------------------------------------------------------------

class LiteEthPHYXGMIIClkPads:
    def __init__(self):
        self.rx = Signal()
        self.tx = Signal()

class LiteEthPHYXGMIIPads:
    def __init__(self):
        self.rx_ctl  = Signal(8)
        self.rx_data = Signal(64)
        self.tx_ctl  = Signal(8)
        self.tx_data = Signal(64)

# LiteEth PHY XGMII TX -----------------------------------------------------------------------------

class LiteEthPHYXGMIITX(LiteXModule):
    def __init__(self, pads, dw, dic=True):
        # Enforce 64-bit data path
        assert dw == 64

        # Sink for data to transmit
        self.sink = sink = stream.Endpoint(eth_phy_description(dw))

        # ---------- Generic signals ----------

        # Masked last_be signal of current clock cycle. last_be should only be
        # respected when last is also asserted.
        masked_last_be = Signal.like(sink.last_be)
        self.comb += [
            If(sink.last,
                masked_last_be.eq(sink.last_be),
            ),
        ]

        # ---------- Inter-frame gap state ----------

        # State to keep track of the current inter-frame gap we are required to
        # maintain. We must take care to always have an inter-frame gap of at
        # least 96 bits (12 bytes), with an exception for the deficit idle gap
        # mechanism. Because XGMII transactions can only start on the first or
        # fifth byte in a 64-bit bus word, it's sufficient to represent this as
        # - 0: less than 4 bytes of IFG transmitted
        # - 1: less than 8 bytes of IFG transmitted
        # - 2: less than 12 bytes of IFG transmitted
        # - 3: 12 or more bytes of IFG transmitted
        current_ifg = Signal(max=4, reset=3)
        next_ifg = Signal(max=4)

        # Shortcut "functions" to add a 32-bit or 64-bit idle bus word to the
        # current inter-frame gap without worring about wrapping, or to reset it
        # (typically on the start of a new transmission). This can be useful to
        # see the effect on the next_ifg value and thus make decisions about
        # subsequent signal states (e.g. sink.ready).
        ifg_reset = Signal()
        ifg_add_double = Signal()
        ifg_add_single = Signal()
        self.comb += [
            If(ifg_reset,
                next_ifg.eq(0),
            ).Elif(ifg_add_single,
                If(current_ifg < 3,
                    next_ifg.eq(current_ifg + 1)
                ),
            ).Elif(ifg_add_double,
                If(current_ifg < 2,
                    next_ifg.eq(current_ifg + 2),
                ).Else(
                    next_ifg.eq(3),
                ),
            ).Else(
                next_ifg.eq(current_ifg),
            ),
        ]

        self.sync += current_ifg.eq(next_ifg)

        # ---------- Deficit idle count mechanism state ----------

        # Because XGMII only allows start of frame characters to be placed on
        # lane 0 (first and fifth octet in a 64-bit bus word), when a packet's
        # length % 4 != 0, we can't transmit exactly 12 XGMII idle characters
        # inter-frame gap (the XGMII end of frame character counts towards the
        # inter-frame gap, while start of frame does not). Given we are required
        # to transmit a minimum of 12 bytes IFG, it's allowed to send packet
        # length % 4 bytes additional IFG bytes. However this would waste
        # precious bandwidth transmitting these characters.
        #
        # Thus, 10Gbit/s Ethernet and above allow using the deficit idle count
        # mechanism. It allows to delete some idle characters, as long as an
        # average count of >= 12 bytes IFG is maintained. This is to be
        # implemented as a two bit counter as specified in IEEE802.3-2018,
        # section four, 46.3.1.4 Start control character alignment.
        #
        # This module implements the deficit idle count algorithm as described
        # by Eric Lynskey of the UNH InterOperability Lab[1]:
        #
        # | current |             |             |             |             |
        # | count   |           0 |           1 |           2 |           3 |
        # |---------+-----+-------+-----+-------+-----+-------+-----+-------|
        # |         |     | new   |     | new   |     | new   |     | new   |
        # | pkt % 4 | IFG | count | IFG | count | IFG | count | IFG | count |
        # |---------+-----+-------+-----+-------+-----+-------+-----+-------|
        # |       0 |  12 |     0 |  12 |     1 |  12 |     2 |  12 |     3 |
        # |       1 |  11 |     1 |  11 |     2 |  11 |     3 |  15 |     0 |
        # |       2 |  10 |     2 |  10 |     3 |  14 |     0 |  14 |     1 |
        # |       3 |   9 |     3 |  13 |     0 |  13 |     1 |  13 |     2 |
        #
        # [1]: https://www.iol.unh.edu/sites/default/files/knowledgebase/10gec/10GbE_DIC.pdf


        # Additional state to keep track of exactly how many bytes % 4 we've
        # transmitted in the last packet. We need this information to judge
        # whether we've had a sufficiently large IFG given the current DIC
        # count. Value the range of [0; 3].
        #
        # If we disable the deficit idle count, we replace this with a constant
        # of 0, meaning that we pretend to not have transmitted any additional
        # IDLE characters. This should allow significant logic optimizations
        # while having the same effect as not implementing DIC at all.
        if dic:
            last_packet_rem = Signal(max=4)
        else:
            last_packet_rem = Constant(0, bits_sign=2)

        # Bounded counter of deleted XGMII idle characters. Must be within [0;
        # 3]. If we disable the deficit idle count mechanism, this signal should
        # not change. However, it's still present to avoid the logic below
        # getting too complex.
        current_dic = Signal(max=4, reset=3)


        # ---------- Shifted transmit state ----------

        # Whether the current transmission is shifted, meaning that the packet's
        # transmission started on the fifth octect within the 64-bit bus
        # word. As a consequence of the shifted transmission, given that we
        # receive 64 valid bits from the sink, we need to store and delay the
        # upper half of the current clock cycle's data to the next.
        #
        # This register is to be set when transitioning out of the IDLE
        # state.
        transmit_shifted = Signal()

        # Upper half of the data of the previous clock cycle.
        prev_valid_data = Signal(dw)
        prev_valid_last_be = Signal(dw // 8)
        self.sync += [
            If(sink.valid & sink.ready,
               prev_valid_data.eq(sink.data),
               If(sink.last,
                  prev_valid_last_be.eq(masked_last_be)
               ).Else(
                   prev_valid_last_be.eq(0),
               ),
            ),
        ]

        # Previous clock cycle sink valid signal
        prev_valid = Signal()
        self.sync += prev_valid.eq(sink.valid)

        # Adjusted sink data & last_be. If our transmission is shifted, this
        # will contain the upper-half of the previous and lower-half of the
        # current clock cycle. Otherwise, simply equal to data and the masked
        # last_be.
        adjusted_sink_valid = Signal()
        adjusted_sink_valid_data = Signal.like(sink.data)
        adjusted_sink_valid_last_be = Signal.like(sink.last_be)
        self.comb += [
            If(transmit_shifted,
                # Because we are injecting data from the previous cycle, we need
                # to respect it's valid. It's fine that adjusted_sink_valid
                # therefore is deasserted for the very first bus word, given
                # this is handled in the IDLE fsm state still. This assumes a
                # non-hostile sink where valid is constantly asserted during a
                # single transmission.
                adjusted_sink_valid.eq(prev_valid),
                adjusted_sink_valid_data.eq(Cat(
                    prev_valid_data[(dw // 2):],
                    sink.data[:(dw // 2)],
                )),
                adjusted_sink_valid_last_be.eq(Cat(
                    prev_valid_last_be[(dw // 8 // 2):],
                    masked_last_be[:(dw // 8 // 2)],
                )),
            ).Else(
                adjusted_sink_valid.eq(sink.valid),
                adjusted_sink_valid_data.eq(sink.data),
                adjusted_sink_valid_last_be.eq(masked_last_be),
            ),
        ]

        # ---------- XGMII transmission logic ----------

        # Transmit FSM
        self.fsm = fsm = FSM(reset_state="IDLE")

        # This block will be executed by the FSM below in the IDLE state, when
        # it's time to start a transmission aligned on the FIRST byte in a
        # 64-bit bus word. This can happen both because we've waited the 12 byte
        # IFG and coincidentally the first byte is the next valid start point,
        # or because we reduced the IFG to 8 bytes because of the deficit idle
        # count mechanism. Thus have it as a reusable component here.
        unshifted_idle_transmit = [
            # Currently idling, but a new frame is ready for transmission
            # and we had at least the full IFG idle before. Thus transmit
            # the preamble, but replace the first byte with the XGMII start
            # of frame control character. Accept more data.
            ifg_reset.eq(1),
            pads.tx_ctl.eq(0x01),
            pads.tx_data.eq(Cat(XGMII_START, sink.data[8:dw])),
            NextValue(transmit_shifted, 0),
            NextValue(sink.ready, 1),
            NextState("TRANSMIT"),
        ]

        # This block will be executed by the FSM below in the IDLE state, when
        # it's time to start a transmission aligned on the FIFTH byte in a
        # 64-bit bus word. This can happen either because we've waited the 8
        # byte IFG and need to insert only four bytes more in this cycle, or
        # because the deficit idle count mechanism allows transmit with a
        # smaller IFG (e.g. 1 bits packet remainder + 4 bytes TRANSMIT IFG in
        # previous cycle + 4 bytes IDLE ID in current cycle = 9 bytes total
        # IFG).
        shifted_idle_transmit = [
            # Currently idling, but a new frame is ready for transmission and
            # there is only 4 bytes missing in the IFG (or we have created an
            # acceptable IFG deficit). Thus transmit the preamble on the second
            # 32-bit bus word, but replace the first byte with the XGMII start
            # of frame control character. Accept more data.
            pads.tx_ctl.eq(0x1F),
            pads.tx_data.eq(Cat(
                Replicate(XGMII_IDLE, 4),
                XGMII_START,
                sink.data[8:(dw // 2)],
            )),
            ifg_reset.eq(1),
            NextValue(transmit_shifted, 1),
            NextValue(sink.ready, 1),
            NextState("TRANSMIT"),
        ]

        fsm.act("IDLE",
            If(sink.valid & (current_ifg == 3),
               # Branch A: we've transmitted at least the full 12 bytes
               # IFG. This means that we can unconditionally start transmission
               # on the first octet. In addition to that, we may have inserted
               # some extra IFG, thus we can reduce the deficit.
               *unshifted_idle_transmit,
               If(current_dic - last_packet_rem < 0,
                   NextValue(current_dic, 0),
               ).Else(
                   NextValue(current_dic, current_dic - last_packet_rem),
               )
            ).Elif(sink.valid & (current_ifg == 2),
                # Branch B: we've transmitted at least 8 bytes of IFG. This
                # means that we can either, depending on the DIC start
                # transmission on the first or fith octect. Manipulate the DIC
                # count accordingly.
                If((last_packet_rem != 0)
                   & (current_dic + last_packet_rem <= 3),
                    # We've taken some extra IFG bytes (added to the deficit)
                    *unshifted_idle_transmit,
                    NextValue(current_dic, current_dic + last_packet_rem),
                ).Else(
                    # We might have inserted some extra IFG bytes (subtracted
                    # from the deficit)
                    *shifted_idle_transmit,
                    If(current_dic - last_packet_rem < 0,
                        NextValue(current_dic, 0),
                    ).Else(
                        NextValue(current_dic, current_dic - last_packet_rem),
                    )
                ),
            ).Elif(sink.valid & (current_ifg == 1) & (last_packet_rem != 0)
                   & (current_dic + last_packet_rem <= 3),
                # Branch C: we've transmitted at least 4 bytes of IFG. Whether
                # we can start a new transmission here depends on the DIC. In
                # any case, we're deleting at least one XGMII idle character,
                # which we need to keep track of. Furthermore, transmission can
                # only ever start on the fifth octect here.
                *shifted_idle_transmit,
                NextValue(current_dic, current_dic + last_packet_rem),
            ).Else(
                # Idling, transmit XGMII IDLE control characters only and add
                # them to the IFG.
                pads.tx_ctl.eq(0xFF),
                pads.tx_data.eq(Cat(*([XGMII_IDLE] * 8))),
                ifg_add_double.eq(1),

                # Accept more data if we've had a sufficiently large inter-frame
                # gap (accounting for deficit idle count). For this we need to
                # determine whether the next sink.valid clock cycle will take a
                # given branch of A, B or C.
                If((next_ifg >= 2)
                   | ((next_ifg == 1) & (last_packet_rem != 0)
                      & (current_dic + last_packet_rem <= 3)),
                    # Branch A, B or C will be taken as soon as sink.valid
                    # again, thus accept more data.
                    NextValue(sink.ready, 1),
                ).Else(
                    # We haven't transmitted a sufficient IFG. The next
                    # sink.valid clock cycle will not start a transmission.
                    NextValue(sink.ready, 0),
                ),

                # If we've remained in IDLE because the sink is not yet valid,
                # even though the full IFG has been sent already, remove any
                # deficit idle count. We've made up for that by now.
                If(current_ifg >= 2,
                    NextValue(current_dic, 0),
                ),

                NextState("IDLE"),
            )
        )

        # How many bytes % 4 we've transmitted in the current packet. This
        # signal is to be asserted when the packet ends in the current clock
        # cycle.
        #
        # If we disable the deficit idle count, we replace this with a constant
        # of 0, meaning that we pretend to not have transmitted any additional
        # IDLE characters. This should allow significant logic optimizations.
        if dic:
            current_packet_rem = Signal(max=4)
        else:
            current_packet_rem = Constant(0, bits_sign=2)

        # Wether the current transmission must be ended in the next clock
        # cycle. This might be required if we haven't transmitted the XGMII end
        # of frame control character, but send all other data of the packet.
        end_transmission = Signal()

        fsm.act("TRANSMIT",
            # Check whether the data is still valid first or we are are not
            # ready to accept a new transmission.
            If(end_transmission | ~adjusted_sink_valid,
                # Data isn't valid, but we're still in the transmit state. This
                # can happen because we've finished transmitting all packet
                # data, but must still transmit the XGMII end of frame control
                # character. Thus put this control character and IDLE on the
                # line, return to IDLE afterwards.
                pads.tx_ctl.eq(0xFF),
                pads.tx_data.eq(Cat(XGMII_END, Replicate(XGMII_IDLE, 7))),
                # Also, we're transmitting 64 bits worth of idle characters.
                ifg_add_double.eq(1),
                # We're transmitting 8 bytes of IFG in this cycle. Thus we know
                # that in the next cycle we can for sure start a new
                # transmission, irrespective of whether we use DIC (either on
                # the first or fifth byte in the 64-bit word). Thus set
                # sink.ready accordingly.
                NextValue(sink.ready, 1),
                # Packet transmission is complete, return to IDLE and reset the
                # end_transmission register.
                NextValue(end_transmission, 0),
                NextState("IDLE"),
            ).Else(
                # The data is valid. For each byte, determine whether it is
                # valid or must be an XGMII idle or end of frame control
                # character based on the value of last_be.
                *[
                    If((adjusted_sink_valid_last_be == 0)
                       | (adjusted_sink_valid_last_be >= (1 << i)),
                        # Either not the last data word or last_be indicates
                        # this byte is still valid
                        pads.tx_ctl[i].eq(0),
                        pads.tx_data[8*i:8*(i+1)].eq(
                            adjusted_sink_valid_data[8*i:8*(i+1)]
                        ),
                    ).Elif((adjusted_sink_valid_last_be == (1 << (i - 1)))
                           if i > 0 else 0,
                        # last_be indicates that this byte is the first one
                        # which is no longer valid, hence transmit the XGMII end
                        # of frame character
                        pads.tx_ctl[i].eq(1),
                        pads.tx_data[8*i:8*(i+1)].eq(XGMII_END),
                        # Also, starting from this character, the inter-frame
                        # gap starts. Depending on where we are in the bus word
                        # (index 0 to 4) we can already count cycle as one
                        # 32-bit IFG step (the XGMII end of frame character
                        # counts towards the IFG).
                        If(i < 5,
                            ifg_add_single.eq(1),
                        ),
                        # If the DIC mechanism is enabled, furthermore keep
                        # track of the remainder (mod 4) of IDLE bytes being
                        # sent.
                        *([
                            current_packet_rem.eq(i % 4),
                            NextValue(last_packet_rem, i % 4),
                        ] if dic else []),
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
                If(adjusted_sink_valid_last_be == 0,
                    # This hasn't been the last bus word. However, before we can
                    # tell the data sink to send us additional data, in case
                    # we're performing a shifted transmission, we must see
                    # whether the current sink data word already indicates the
                    # end of data in it's upper half. If so, we must not request
                    # additional data. Otherwise we could loose valid data, as
                    # we're transmitting the IFG first.
                    If(transmit_shifted & sink.last
                       & ((sink.last_be & 0xF0) != 0),
                        # We're in a shifted transmit and already have received
                        # the last data bytes from the sink.
                        NextValue(sink.ready, 0),
                    ).Else(
                        # Everything's good, the sink hasn't yet asserted last.
                        NextValue(sink.ready, 1),
                    ),
                    NextState("TRANSMIT"),
                ).Elif(adjusted_sink_valid_last_be == (1 << 7),
                    # Last data word, but all bytes were valid. Thus we still
                    # need to transmit the XGMII end control character.
                    NextValue(end_transmission, 1),
                    NextValue(sink.ready, 0),
                    NextState("TRANSMIT"),
                ).Else(
                    # We did already transmit the XGMII end control
                    # character. Depending on the interframegap sent as part of
                    # this cycle and the current deficit idle count, we might
                    # already be able to accept data in the next clock cycle.
                    If((next_ifg >= 2)
                       | ((next_ifg == 1) & (last_packet_rem != 0)
                          & (current_dic + last_packet_rem <= 3)),
                        NextValue(sink.ready, 1),
                    ).Else(
                        NextValue(sink.ready, 0),
                    ),
                    NextState("IDLE"),
                )
            )
        )

# LiteEth PHY XGMII RX Aligner ---------------------------------------------------------------------

class LiteEthPHYXGMIIRXAligner(LiteXModule):
    def __init__(self, unaligned_ctl, unaligned_data):
        # Aligned ctl and data characters
        self.aligned_ctl = Signal.like(unaligned_ctl)
        self.aligned_data = Signal.like(unaligned_data)

        # Buffer for low-bytes of the last XGMII bus word
        low_ctl = Signal(len(unaligned_ctl) // 2)
        low_data = Signal(len(unaligned_data) // 2)


        # Alignment FSM
        self.fsm = fsm = FSM(reset_state="NOSHIFT")

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

# LiteEth PHY XGMII RX -----------------------------------------------------------------------------

class LiteEthPHYXGMIIRX(LiteXModule):
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
        self.aligner = LiteEthPHYXGMIIRXAligner(pads.rx_ctl, pads.rx_data)

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
        self.fsm = fsm = FSM(reset_state="IDLE")

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

# LiteEth PHY XGMII CRG ----------------------------------------------------------------------------

class LiteEthPHYXGMIICRG(LiteXModule):
    def __init__(self, clock_pads, model=False):
        self._reset = CSRStorage()
        self.cd_eth_rx = ClockDomain()
        self.cd_eth_tx = ClockDomain()
        if model:
            self.comb += [
                self.cd_eth_rx.clk.eq(ClockSignal()),
                self.cd_eth_tx.clk.eq(ClockSignal())
            ]
        else:
            self.comb += [
                self.cd_eth_rx.clk.eq(clock_pads.rx),
                self.cd_eth_tx.clk.eq(clock_pads.tx),
            ]

# LiteEth PHY XGMII --------------------------------------------------------------------------------

class LiteEthPHYXGMII(LiteXModule):
    dw          = 8
    tx_clk_freq = 156.25e6
    rx_clk_freq = 156.25e6
    def __init__(self, clock_pads, pads, model=False, dw=64, with_hw_init_reset=True, dic=True):
        self.dw = dw
        self.cd_eth_tx, self.cd_eth_rx = "eth_tx", "eth_rx"
        self.integrated_ifg_inserter = True
        self.crg = LiteEthPHYXGMIICRG(clock_pads, model)
        self.tx = ClockDomainsRenamer(self.cd_eth_tx)(LiteEthPHYXGMIITX(
            pads = pads,
            dw   = self.dw,
            dic  = dic,
        ))
        self.rx = ClockDomainsRenamer(self.cd_eth_rx)(LiteEthPHYXGMIIRX(
            pads = pads,
            dw   = self.dw,
        ))
        self.sink, self.source = self.tx.sink, self.rx.source
