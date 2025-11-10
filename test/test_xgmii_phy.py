#
# This file is part of LiteEth.
#
# Copyright (c) 2021 Leon Schuermann <leon@is.currently.online>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random
import csv
from pathlib import Path

from migen import *

from litex.soc.interconnect.stream import *
from liteeth.phy.xgmii import LiteEthPHYXGMII, LiteEthPHYXGMIIRX

from .test_stream import StreamPacket, stream_inserter, stream_collector, compare_packets

# Helper -------------------------------------------------------------------------------------------

def mask_last_be(dw, data, last_be):
    """Mark some data by a last_be data qualifier. The rest of the data
    passed in will be zeroed.
    """
    masked_data = 0

    for byte in range(dw // 8):
        if 2**byte > last_be:
            break
        masked_data |= data & (0xFF << (byte * 8))

    return masked_data

# XGMII Collector ----------------------------------------------------------------------------------

class XGMIICollector:
    def __init__(self, min_interframegap=12, tolerate_dic=True, debug_print=False):
        # Minimum IFG legal to be accepted on the XGMII interface (excluding
        # DIC, if tolerated). On the receiving send, when accounting for
        # potential IFG shrinkage and allowing the minimum receive IFG as
        # mandated by IEEE 802.3 (e.g. `min_interframegap` = 5 bytes IFG for
        # 10Gbit/s Ethernet), tolerate_dic should thus be disabled.
        self.min_interframegap = min_interframegap

        # Whether the collector should spit out debug information about received
        # signal states. Will always print error conditions.
        self.debug_print = debug_print

        # Whether the deficit idle count mechanism should be tolerated. This
        # will allow the receiver to temporarily accept IFGs < 12 bytes, as long
        # as an average inter-frame gap of >= 12 is maintained. This must be
        # implemented as a 2-bit counter, as per IEEE 802.3-2018, section four,
        # 46.3.1.4 Start control character alignment.
        self.tolerate_dic = tolerate_dic

        # Proper deficit idle count, implemented as a two bit counter using the
        # algorithm described by Eric Lynskey of the UNH InterOperability
        # Lab[1]:
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
        self.dic = 0

        # How many additional IDLE characters we've seen. We are faithful that
        # the device is complying and initialize this to the mandated IFG byte
        # count + 3 bytes extra IDLE characters inserted through DIC, in case we
        # listen in to a captured stream and a new packet starts immediately.
        self.interframegap = 15

        # Received packets, array of arrays of bytes.
        self.packets = []

        # Packet currently being received. Array of bytes.
        self.current_packet = None

        # History of observed inter-frame gaps for debugging purposes.
        self.interframegaps = []

        # Whether the collector is currently collecting data or done. This can
        # be very useful information for designing composite test systems and
        # running until all data has been gathered.
        self.collecting = False

    def inject_32b_bus_word(self, ctl_word, data_word):
        for i in range(4):
            ctl = (ctl_word >> i) & 1
            data = (data_word >> (i * 8)) & 0xFF

            if ctl == 0 and self.current_packet is not None:
                # Data byte _and_ currently reading a packet, all fine!
                self.current_packet += [data]

            elif ctl == 1 and data == 0xFB:
                # XGMII start of frame control character
                if self.current_packet is not None:
                    raise ValueError("Got start of frame control character "
                                     + "while reading packet")

                if i != 0:
                    raise ValueError("Got start of frame control character on "
                                     + "lane {}".format(i))

                # Check and validate the observed IFG
                if self.tolerate_dic:
                    if self.interframegap < self.min_interframegap:
                        # Produced some deficit, check if it's legal
                        self.dic += self.min_interframegap - self.interframegap
                        if self.dic > 3:
                            raise ValueError("DIC bounds exceeded. Observed {} "
                                             + "bytes IFG, but DIC would have "
                                             + "allowed {}.".format(
                                                 self.interfamegap,
                                                 3 - self.dic))

                    elif self.interframegap > self.min_interframegap:
                        # Inserted some extra IDLE, subtract from the deficit
                        self.dic = min(0, self.dic - (
                            self.interframegap - self.min_interframegap
                        ))

                elif self.interframegap < self.min_interframegap:
                    # DIC is disabled
                    raise ValueError("IFG violated. Oberserved {} bytes, which "
                                     + "is less than the minimum of {}".format(
                                         self.interframegap,
                                         self.min_interframegap
                                     ))

                # Store the observed IFG for debugging purposes and reset it
                self.interframegaps += [self.interframegap]
                self.interframegap = 0

                # Start a new packet. The XGMII start of frame character
                # replaces the first preamble octet, so store that as the first
                # byte.
                self.current_packet = [0x55]

            elif ctl == 1 and data == 0xFD:
                # XGMII end of frame control character

                if self.current_packet is None:
                    if len(self.packets) == 0 and self.debug_print:
                        print("INFO: got end of frame control character while "
                              + "not reading a packet. This can be valid for "
                              + "the first partial packet in the capture, but "
                              + "not afterwards.")
                    elif len(self.packets) != 0:
                        raise ValueError("Got end of frame control character "
                                         + "while not reading a packet.")
                else:
                    if self.debug_print:
                        print("Received XGMII packet {}.".format(
                            len(self.packets)
                        ))

                    # Transmission ended, store the packet and reset the current
                    # packet.
                    self.packets += [self.current_packet]
                    self.current_packet = None

                    # The XGMII end of frame control character does count
                    # towards the IFG
                    self.interframegap = 1

                    # All following bytes MUST be XGMII IDLE control
                    # characters. We will want to verify that and
                    # count the number of bytes until i % 4 == 0
                    # towards the DIC counter.
                    end_of_frame = True

            elif ctl == 1 and data == 0x07:
                # XGMII idle control character

                if self.current_packet is not None:
                    raise ValueError("Got idle control character in the middle "
                                     + "of a packet")

                self.interframegap += 1

            elif ctl == 1:
                # Unrecognized XGMII control character

                raise ValueError("Invalid XGMII control character {:02x}"
                                 .format(data))

    def inject_bus_word(self, dw, ctl_word, data_word):
        if dw == 32:
            self.inject_32b_bus_word(ctl_word, data_word)
        elif dw == 64:
            self.inject_32b_bus_word(
                ctl_word & 0xF,
                data_word & 0xFFFFFFFF
            )
            self.inject_32b_bus_word(
                (ctl_word >> 4) & 0xF,
                (data_word >> 32) & 0xFFFFFFFF,
            )
        else:
            raise ValueError("Unknown data width: {} bits".format(dw))


    def collect(self, xgmii_interface, tap_signals="tx", stop_cond=None):
        self.collecting = True

        # Which signals to attach to in the passed XGMII interface
        assert tap_signals in ["tx", "rx"]

        if stop_cond is None:
            stop_cond = lambda: False

        while not stop_cond():
            if tap_signals == "tx":
                ctl_word = yield xgmii_interface.tx_ctl
                data_word = yield xgmii_interface.tx_data
                dw = len(xgmii_interface.tx_data)
            elif tap_signals == "rx":
                ctl_word = yield xgmii_interface.rx_ctl
                data_word = yield xgmii_interface.rx_data
                dw = len(xgmii_interface.rx_data)
            self.inject_bus_word(dw, ctl_word, data_word)
            yield

        self.collecting = False

# XGMII 64b CSV Reader -----------------------------------------------------------------------------

class XGMII64bCSVReader:
    def __init__(self, filename, extract_signals_pattern="rx",
                 complete_trailing_transaction=True):
        # Whether we should attempt to complete a trailing XGMII transaction by
        # inserting an XGMII end control character.
        self.complete_trailing_transaction = complete_trailing_transaction

        # Store filename for reference
        self.filename = filename

        # Open the CSV file and create a CSV reader over it
        self.filehandle = open(filename, 'r')
        self.reader = csv.reader(
            # Support comments in the CSV file
            filter(lambda line: not line.startswith("#"), self.filehandle),
            delimiter=','
        )

        # Extract the headers and correlate them with the required signal
        # pattern to find out the matching columns
        self.column_headers = next(self.reader, None)
        assert self.column_headers is not None, \
            "Failed to load column header row from CSV"

        self.rx_ctl_col = None
        self.rx_data_col = None
        for i, header in enumerate(self.column_headers):
            if "{}_ctl".format(extract_signals_pattern) in header:
                self.rx_ctl_col = i
            elif "{}_data".format(extract_signals_pattern) in header:
                self.rx_data_col = i

        assert self.rx_ctl_col is not None, \
            "Failed to find RX CTL signal column in CSV"
        assert self.rx_data_col is not None, \
            "Failed to find RX DATA signal column in CSV"

        self.datatype_headers = next(self.reader, None)
        assert self.datatype_headers is not None, \
            "Failed to load data type header row from CSV"
        assert self.datatype_headers[self.rx_ctl_col] == "HEX", \
            "XGMII CTL signal is not hex-encoded"
        assert self.datatype_headers[self.rx_data_col] == "HEX", \
            "XGMII DATA signal is not hex-encoded"

        # Whether the inserter is currently in the middle of a packet
        self.currently_in_packet = False

        # Hack around Python not having a peekable iterator by always taking the
        # next element at the end of a function.
        self.next_row = next(self.reader, None)

        # Upper XGMII bus word, for when 32 bit words are accessed
        self.upper_ctl = None
        self.upper_data = None

    def __del__(self):
        self.filehandle.close()

    def get_64b_bus_word(self):
        if self.next_row is None:
            return None

        ctl_word = int(self.next_row[self.rx_ctl_col], 16)
        data_word = int(self.next_row[self.rx_data_col], 16)

        new_packet = False

        # Detect whether this is just starting a new packet
        for i in range(8):
            ctl = (ctl_word >> i) & 1
            data = (data_word >> (i * 8)) & 0xFF

            if ctl == 1 and data == 0xFB:
                new_packet = True
                self.currently_in_packet = True

            if ctl == 1 and data == 0xFD:
                self.currently_in_packet = False

        # Store for below
        prev_row = self.next_row
        self.next_row = next(self.reader, None)

        # Let's make sure we have now half / dangling packet at the end. If this
        # was the last column and we're currently receiving one, make sure we
        # end it properly. This might go terribly wrong if this is condition
        # aries at the start of a packet though, in this case just throw an
        # error.
        if self.next_row is None and self.currently_in_packet \
            and self.complete_trailing_transaction:

            if new_packet:
                raise ValueError("CSV ends just at the start of a new packet, "
                                 + "we can't handle that!")

            self.next_row = prev_row
            self.next_row[self.rx_ctl_col] = "ff"
            self.next_row[self.rx_data_col] = "07070707070707fd"

        return (ctl_word, data_word)

    def get_bus_word(self, dw=64):
        if dw == 64:
            assert self.upper_data is None and self.upper_ctl is None, \
                "Cannot query 32bit and 64bit bus words interchangeably"

            return self.get_64b_bus_word()
        elif dw == 64:
            if self.upper_data is not None:
                assert self.upper_ctl is not None

                return (self.upper_ctl, self.upper_data)
            else:
                xgmii_64b = self.get_64b_bus_word()

                self.upper_ctl = (xgmii_64b[0] >> 4) & 0xF
                self.upper_data = (xgmii_64b[1] >> 32) & 0xFFFFFFFF

                return (
                    xgmii_64b[0] & 0xF,
                    xgmii_64b[1] & 0xFFFFFFFF,
                )
        else:
            raise ValueError("Unknown dw {}!".format(dw))

    def done(self):
        return self.next_row is None

    def inject(self, xgmii_interface, stop_cond=None):
        proper_stop_cond = True

        if stop_cond is None:
            proper_stop_cond = False
            stop_cond = lambda: False

        while not self.done():
            if stop_cond():
                return

            (ctl, data) = self.get_bus_word(len(xgmii_interface.rx_data))
            yield xgmii_interface.rx_ctl.eq(ctl)
            yield xgmii_interface.rx_data.eq(data)
            yield

        while self.done() and proper_stop_cond and not stop_cond():
            yield xgmii_interface.rx_ctl.eq(0xFF)
            yield xgmii_interface.rx_data.eq(0x0707070707070707)
            yield

# Test XGMII PHY -----------------------------------------------------------------------------------

class TestXGMIIPHY(unittest.TestCase):
    def test_xgmii_rx(self):
        # Read XGMII data from the CSV file.
        csv_file = Path(__file__).parent / "assets" / "xgmii_bus_capture.csv"
        xgmii_injector = XGMII64bCSVReader(
            csv_file.resolve(),
            complete_trailing_transaction=True
        )

        # Collect the XGMII transactions from the reader with a minimum
        # inter-frame gap of 5 (accounted for potential IFG shrinkage).
        xgmii_collector = XGMIICollector(
            min_interframegap = 5,
            tolerate_dic      = False,
            debug_print       = True,
        )

        # XGMII interface.
        xgmii_interface = Record([
            ("rx_ctl",   8),
            ("rx_data", 64),
            ("tx_ctl",   8),
            ("tx_data", 64),
        ])

        # We simply test the receiver component (XGMII -> stream) here.
        dut = LiteEthPHYXGMIIRX(
            xgmii_interface,
            64,
        )

        recvd_packets = []
        run_simulation(
            dut, [
                xgmii_injector.inject(
                    xgmii_interface,
                ),
                xgmii_collector.collect(
                    xgmii_interface,
                    tap_signals = "rx",
                    stop_cond   = lambda: xgmii_injector.done() \
                        and xgmii_collector.current_packet is None,
                ),
                stream_collector(
                    dut.source,
                    dest        = recvd_packets,
                    stop_cond   = xgmii_injector.done,
                    seed        = 42,
                    debug_print = True,
                    # The XGMII PHY RX part deliberately does not support a
                    # deasserted ready signal. The sink is assumed to be always
                    # ready.
                    ready_rand  = 0,
                ),
            ],
        )

        self.assertTrue(
            len(recvd_packets) == len(xgmii_collector.packets),
            "Different number of received and sent packets: {} vs. {}!"
            .format(len(recvd_packets), len(xgmii_collector.packets))
        )
        for p, (recvd, sent) in enumerate(
                zip(recvd_packets, xgmii_collector.packets)):
            self.assertTrue(
                len(recvd.data) == len(sent),
                ("Packet sent and received with different length: {} vs. {} "
                 + "at packet {}!"
                ).format(len(recvd.data), len(sent), p)
            )
            for i, (a, b) in enumerate(zip(recvd.data, sent)):
                self.assertTrue(
                    a == b,
                    ("Byte sent and received differ: {} vs. {} at {} byte of "
                     + "packet {}"
                    ).format(a, b, i, p)
                )

    def test_xgmii_stream_loopback(self):
        # Read XGMII data from the CSV file
        csv_file = Path(__file__).parent / "assets" / "xgmii_bus_capture.csv"
        xgmii_injector = XGMII64bCSVReader(
            csv_file.resolve(),
            complete_trailing_transaction=True
        )

        # Collect the XGMII transactions from the CSV reader with a minimum
        # inter-frame gap of 5 (accounted for potential IFG shrinkage).
        xgmii_rx_collector = XGMIICollector(
            min_interframegap = 5,
            tolerate_dic      = False,
            debug_print       = True
        )

        # Collect the XGMII transactions from the TX PHY with a minimum
        # inter-frame gap of 5. As a dumb loopback (akin to the behavior of a
        # repeater) this is the smallest IPG value which may be put on the wire
        # again.
        xgmii_tx_collector = XGMIICollector(
            min_interframegap = 12,
            tolerate_dic      = True,
            debug_print       = True
        )

        class DUT(Module):
            def __init__(self):
                # XGMII signals
                self.xgmii_interface = Record([
                    ("rx_ctl",   8),
                    ("rx_data", 64),
                    ("tx_ctl",   8),
                    ("tx_data", 64),
                ])

                # PHY with TX and RX side
                self.submodules.ethphy = ClockDomainsRenamer({
                    "eth_tx" : "sys",
                    "eth_rx" : "sys",
                })(LiteEthPHYXGMII(
                    Record([("rx", 1), ("tx", 1)]),
                    self.xgmii_interface,
                    model=True,
                ))

                # Insert a synchronous FIFO to allow some variability of the
                # inter-frame gap. If it overflows we know that we're not
                # processing data at line rate, thus we must make sure that we
                # detect such a case.
                self.submodules.loopback_fifo = SyncFIFO(
                    self.ethphy.source.payload.layout,
                    4,
                    True,
                )

                self.comb += [
                    self.ethphy.source.connect(self.loopback_fifo.sink),
                    self.loopback_fifo.source.connect(self.ethphy.sink),
                ]

        dut = DUT()
        run_simulation(
            dut, [
                xgmii_rx_collector.collect(
                    dut.xgmii_interface,
                    tap_signals = "rx",
                    stop_cond   = lambda: xgmii_injector.done() \
                        and xgmii_rx_collector.current_packet is None,
                ),
                xgmii_tx_collector.collect(
                    dut.xgmii_interface,
                    tap_signals = "tx",
                    stop_cond   = lambda: xgmii_injector.done() \
                        and xgmii_tx_collector.current_packet is None \
                        and len(xgmii_tx_collector.packets) \
                            >= len(xgmii_rx_collector.packets),
                ),
                xgmii_injector.inject(
                    dut.xgmii_interface,
                    stop_cond = lambda: not xgmii_rx_collector.collecting \
                        and not xgmii_tx_collector.collecting,
                ),
            ],
        )

        self.assertTrue(
            len(xgmii_rx_collector.packets) == len(xgmii_tx_collector.packets),
            "Different number of sent and received packets: {} vs. {}!"
            .format(len(xgmii_rx_collector.packets),
                    len(xgmii_tx_collector.packets))
        )
        for p, (recvd, sent) in enumerate(
                zip(xgmii_tx_collector.packets, xgmii_rx_collector.packets)):
            self.assertTrue(
                len(recvd) == len(sent),
                ("Packet sent and received with different length: {} vs. {} at "
                 + "packet {}!"
                ).format(len(recvd), len(sent), p)
            )
            for i, (a, b) in enumerate(zip(recvd, sent)):
                self.assertTrue(
                    a == b,
                    ("Byte sent and received differ: {} vs. {} at {} byte of "
                     + "packet {}"
                    ).format(a, b, i, p)
                )
