# This file is Copyright (c) 2015-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2015-2017 Sebastien Bourdeauducq <sb@m-labs.hk>
# This file is Copyright (c) 2017-2018 whitequark <whitequark@whitequark.org>
# License: BSD

from liteeth.common import *
from liteeth.mac import gap, preamble, crc, padding, last_be
from liteeth.phy.model import LiteEthPHYModel

from migen.genlib.cdc import PulseSynchronizer


class LiteEthMACCore(Module, AutoCSR):
    def __init__(self, phy, dw, endianness="big",
            with_preamble_crc=True,
            with_padding=True):
        if dw < phy.dw:
            raise ValueError("Core data width({}) must be larger than PHY data width({})".format(dw, phy.dw))

        rx_pipeline = [phy]
        tx_pipeline = [phy]

        rx_cd_name = "eth_rx"
        tx_cd_name = "eth_tx"
        if hasattr(phy, 'name'):
            rx_cd_name = phy.name + "_" + rx_cd_name
            tx_cd_name = phy.name + "_" + tx_cd_name

        # Interpacket gap
        tx_gap_inserter = gap.LiteEthMACGap(phy.dw)
        self.submodules += ClockDomainsRenamer(tx_cd_name)(tx_gap_inserter)
        tx_pipeline += [tx_gap_inserter]

        # Preamble / CRC
        if isinstance(phy, LiteEthPHYModel):
            # In simulation, avoid CRC/Preamble to enable direct connection
            # to the Ethernet tap.
            self._preamble_crc = CSRStatus(reset=1)
        elif with_preamble_crc:
            self._preamble_crc = CSRStatus(reset=1)
            self.preamble_errors = CSRStatus(32)
            self.crc_errors = CSRStatus(32)

            # Preamble insert/check
            preamble_inserter = preamble.LiteEthMACPreambleInserter(phy.dw)
            preamble_checker = preamble.LiteEthMACPreambleChecker(phy.dw)
            self.submodules += ClockDomainsRenamer(tx_cd_name)(preamble_inserter)
            self.submodules += ClockDomainsRenamer(rx_cd_name)(preamble_checker)

            # CRC insert/check
            crc32_inserter = crc.LiteEthMACCRC32Inserter(eth_phy_description(phy.dw))
            crc32_checker = crc.LiteEthMACCRC32Checker(eth_phy_description(phy.dw))
            self.submodules += ClockDomainsRenamer(tx_cd_name)(crc32_inserter)
            self.submodules += ClockDomainsRenamer(rx_cd_name)(crc32_checker)

            tx_pipeline += [preamble_inserter, crc32_inserter]
            rx_pipeline += [preamble_checker, crc32_checker]

            # Error counters
            self.submodules.ps_preamble_error = PulseSynchronizer(rx_cd_name, "sys")
            self.submodules.ps_crc_error = PulseSynchronizer(rx_cd_name, "sys")

            self.comb += [
                self.ps_preamble_error.i.eq(preamble_checker.error),
                self.ps_crc_error.i.eq(crc32_checker.error),
            ]
            self.sync += [
                If(self.ps_preamble_error.o,
                    self.preamble_errors.status.eq(self.preamble_errors.status + 1)),
                If(self.ps_crc_error.o,
                    self.crc_errors.status.eq(self.crc_errors.status + 1)),
            ]

        # Padding
        if with_padding:
            padding_inserter = padding.LiteEthMACPaddingInserter(phy.dw, 60)
            padding_checker = padding.LiteEthMACPaddingChecker(phy.dw, 60)
            self.submodules += ClockDomainsRenamer(tx_cd_name)(padding_inserter)
            self.submodules += ClockDomainsRenamer(rx_cd_name)(padding_checker)

            tx_pipeline += [padding_inserter]
            rx_pipeline += [padding_checker]

        # Delimiters
        if dw != 8:
            tx_last_be = last_be.LiteEthMACTXLastBE(phy.dw)
            rx_last_be = last_be.LiteEthMACRXLastBE(phy.dw)
            self.submodules += ClockDomainsRenamer(tx_cd_name)(tx_last_be)
            self.submodules += ClockDomainsRenamer(rx_cd_name)(rx_last_be)

            tx_pipeline += [tx_last_be]
            rx_pipeline += [rx_last_be]

        # Converters
        if dw != phy.dw:
            reverse = endianness == "big"
            tx_converter = stream.StrideConverter(eth_phy_description(dw),
                                                  eth_phy_description(phy.dw),
                                                 reverse=reverse)
            rx_converter = stream.StrideConverter(eth_phy_description(phy.dw),
                                                  eth_phy_description(dw),
                                                  reverse=reverse)
            self.submodules += ClockDomainsRenamer(tx_cd_name)(tx_converter)
            self.submodules += ClockDomainsRenamer(rx_cd_name)(rx_converter)

            tx_pipeline += [tx_converter]
            rx_pipeline += [rx_converter]

        # Cross Domain Crossing
        tx_cdc = stream.AsyncFIFO(eth_phy_description(dw), 64)
        rx_cdc = stream.AsyncFIFO(eth_phy_description(dw), 64)
        self.submodules += ClockDomainsRenamer({"write": "sys", "read": tx_cd_name})(tx_cdc)
        self.submodules += ClockDomainsRenamer({"write": rx_cd_name, "read": "sys"})(rx_cdc)

        tx_pipeline += [tx_cdc]
        rx_pipeline += [rx_cdc]

        # Graph
        self.submodules.tx_pipeline = stream.Pipeline(*reversed(tx_pipeline))
        self.submodules.rx_pipeline = stream.Pipeline(*rx_pipeline)

        self.sink, self.source = self.tx_pipeline.sink, self.rx_pipeline.source
