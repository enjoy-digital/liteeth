#
# This file is part of MiSoC and has been adapted/modified for LiteEth.
#
# Copyright (c) 2018-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer

from liteiclink.transceiver.gtx_7series import GTXChannelPLL, GTXTXInit, GTXRXInit

from liteeth.phy.pcs_1000basex import *


class Open(Signal):
    pass


class Gearbox(Module):
    def __init__(self):
        self.tx_data      = Signal(10)
        self.tx_data_half = Signal(20)
        self.rx_data_half = Signal(20)
        self.rx_data      = Signal(10)

        # TX
        buf = Signal(20)
        self.sync.eth_tx += buf.eq(Cat(buf[10:], self.tx_data))
        self.sync.eth_tx_half += self.tx_data_half.eq(buf)

        # RX
        phase_half       = Signal()
        phase_half_rereg = Signal()
        self.sync.eth_rx_half += phase_half_rereg.eq(phase_half)
        self.sync.eth_rx += [
            If(phase_half == phase_half_rereg,
                self.rx_data.eq(self.rx_data_half[10:])
            ).Else(
                self.rx_data.eq(self.rx_data_half[:10])
            ),
            phase_half.eq(~phase_half),
        ]


# Configured for 200MHz transceiver reference clock
class K7_1000BASEX(Module):
    dw          = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    def __init__(self, refclk_or_clk_pads, data_pads, sys_clk_freq):
        pcs = PCS(lsb_first=True)
        self.submodules += pcs

        self.sink    = pcs.sink
        self.source  = pcs.source
        self.link_up = pcs.link_up

        self.clock_domains.cd_eth_tx      = ClockDomain()
        self.clock_domains.cd_eth_rx      = ClockDomain()
        self.clock_domains.cd_eth_tx_half = ClockDomain(reset_less=True)
        self.clock_domains.cd_eth_rx_half = ClockDomain(reset_less=True)

        # for specifying clock constraints. 62.5MHz clocks.
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        # # #

        if isinstance(refclk_or_clk_pads, Signal):
            refclk = refclk_or_clk_pads
        else:
            refclk = Signal()
            self.specials += Instance("IBUFDS_GTE2",
                i_I   = refclk_or_clk_pads.p,
                i_IB  = refclk_or_clk_pads.n,
                i_CEB = 0,
                o_O   = refclk
            )

        # GTX transceiver
        tx_reset       = Signal()
        tx_mmcm_locked = Signal()
        tx_data        = Signal(20)
        tx_reset_done  = Signal()

        rx_reset       = Signal()
        rx_mmcm_locked = Signal()
        rx_data        = Signal(20)
        rx_reset_done  = Signal()

        pll = GTXChannelPLL(refclk, 200e6, 1.25e9)
        self.submodules.pll = pll

        # Work around Python's 255 argument limitation.
        gtx_params = dict(
            # Simulation-Only Attributes
            p_SIM_RECEIVER_DETECT_PASS   ="TRUE",
            p_SIM_TX_EIDLE_DRIVE_LEVEL   ="X",
            p_SIM_RESET_SPEEDUP          ="FALSE",
            p_SIM_CPLLREFCLK_SEL         ="FALSE",
            p_SIM_VERSION                ="4.0",

            # RX Byte and Word Alignment Attributes
            p_ALIGN_COMMA_DOUBLE                     = "FALSE",
            p_ALIGN_COMMA_ENABLE                     = 0b1111111111,
            p_ALIGN_COMMA_WORD                       = 2,
            p_ALIGN_MCOMMA_DET                       = "TRUE",
            p_ALIGN_MCOMMA_VALUE                     = 0b1010000011,
            p_ALIGN_PCOMMA_DET                       = "TRUE",
            p_ALIGN_PCOMMA_VALUE                     = 0b0101111100,
            p_SHOW_REALIGN_COMMA                     = "TRUE",
            p_RXSLIDE_AUTO_WAIT                      = 7,
            p_RXSLIDE_MODE                           = "OFF",
            p_RX_SIG_VALID_DLY                       = 10,

            # RX 8B/10B Decoder Attributes
            p_RX_DISPERR_SEQ_MATCH                   = "TRUE",
            p_DEC_MCOMMA_DETECT                      = "TRUE",
            p_DEC_PCOMMA_DETECT                      = "TRUE",
            p_DEC_VALID_COMMA_ONLY                   = "TRUE",

            # RX Clock Correction Attributes
            p_CBCC_DATA_SOURCE_SEL                   = "DECODED",
            p_CLK_COR_SEQ_2_USE                      = "FALSE",
            p_CLK_COR_KEEP_IDLE                      = "FALSE",
            p_CLK_COR_MAX_LAT                        = 9,
            p_CLK_COR_MIN_LAT                        = 7,
            p_CLK_COR_PRECEDENCE                     = "TRUE",
            p_CLK_COR_REPEAT_WAIT                    = 0,
            p_CLK_COR_SEQ_LEN                        = 1,
            p_CLK_COR_SEQ_1_ENABLE                   = 0b1111,
            p_CLK_COR_SEQ_1_1                        = 0b0100000000,
            p_CLK_COR_SEQ_1_2                        = 0b0000000000,
            p_CLK_COR_SEQ_1_3                        = 0b0000000000,
            p_CLK_COR_SEQ_1_4                        = 0b0000000000,
            p_CLK_CORRECT_USE                        = "FALSE",
            p_CLK_COR_SEQ_2_ENABLE                   = 0b1111,
            p_CLK_COR_SEQ_2_1                        = 0b0100000000,
            p_CLK_COR_SEQ_2_2                        = 0b0000000000,
            p_CLK_COR_SEQ_2_3                        = 0b0000000000,
            p_CLK_COR_SEQ_2_4                        = 0b0000000000,

            # RX Channel Bonding Attributes
            p_CHAN_BOND_KEEP_ALIGN                   = "FALSE",
            p_CHAN_BOND_MAX_SKEW                     = 1,
            p_CHAN_BOND_SEQ_LEN                      = 1,
            p_CHAN_BOND_SEQ_1_1                      = 0b0000000000,
            p_CHAN_BOND_SEQ_1_2                      = 0b0000000000,
            p_CHAN_BOND_SEQ_1_3                      = 0b0000000000,
            p_CHAN_BOND_SEQ_1_4                      = 0b0000000000,
            p_CHAN_BOND_SEQ_1_ENABLE                 = 0b1111,
            p_CHAN_BOND_SEQ_2_1                      = 0b0000000000,
            p_CHAN_BOND_SEQ_2_2                      = 0b0000000000,
            p_CHAN_BOND_SEQ_2_3                      = 0b0000000000,
            p_CHAN_BOND_SEQ_2_4                      = 0b0000000000,
            p_CHAN_BOND_SEQ_2_ENABLE                 = 0b1111,
            p_CHAN_BOND_SEQ_2_USE                    = "FALSE",
            p_FTS_DESKEW_SEQ_ENABLE                  = 0b1111,
            p_FTS_LANE_DESKEW_CFG                    = 0b1111,
            p_FTS_LANE_DESKEW_EN                     = "FALSE",

            # RX Margin Analysis Attributes
            p_ES_CONTROL                             = 0b000000,
            p_ES_ERRDET_EN                           = "FALSE",
            p_ES_EYE_SCAN_EN                         = "TRUE",
            p_ES_HORZ_OFFSET                         = 0x000,
            p_ES_PMA_CFG                             = 0b0000000000,
            p_ES_PRESCALE                            = 0b00000,
            p_ES_QUALIFIER                           = 0x00000000000000000000,
            p_ES_QUAL_MASK                           = 0x00000000000000000000,
            p_ES_SDATA_MASK                          = 0x00000000000000000000,
            p_ES_VERT_OFFSET                         = 0b000000000,

            # FPGA RX Interface Attributes
            p_RX_DATA_WIDTH                          = 20,

            # PMA Attributes
            p_OUTREFCLK_SEL_INV                      = 0b11,
            p_PMA_RSV                                = 0x001e7080,
            p_PMA_RSV2                               = 0x2050,
            p_PMA_RSV3                               = 0b00,
            p_PMA_RSV4                               = 0x00000000,
            p_RX_BIAS_CFG                            = 0b000000000100,
            p_DMONITOR_CFG                           = 0x000A00,
            p_RX_CM_SEL                              = 0b11,
            p_RX_CM_TRIM                             = 0b010,
            p_RX_DEBUG_CFG                           = 0b000000000000,
            p_RX_OS_CFG                              = 0b0000010000000,
            p_TERM_RCAL_CFG                          = 0b10000,
            p_TERM_RCAL_OVRD                         = 0b0,
            p_TST_RSV                                = 0x00000000,
            p_RX_CLK25_DIV                           = 5,
            p_TX_CLK25_DIV                           = 5,
            p_UCODEER_CLR                            = 0b0,

            # PCI Express Attributes
            p_PCS_PCIE_EN                            = "FALSE",

            # PCS Attributes
            p_PCS_RSVD_ATTR                          = 0x000000000000,

            # RX Buffer Attributes
            p_RXBUF_ADDR_MODE                        = "FAST",
            p_RXBUF_EIDLE_HI_CNT                     = 0b1000,
            p_RXBUF_EIDLE_LO_CNT                     = 0b0000,
            p_RXBUF_EN                               = "TRUE",
            p_RX_BUFFER_CFG                          = 0b000000,
            p_RXBUF_RESET_ON_CB_CHANGE               = "TRUE",
            p_RXBUF_RESET_ON_COMMAALIGN              = "FALSE",
            p_RXBUF_RESET_ON_EIDLE                   = "FALSE",
            p_RXBUF_RESET_ON_RATE_CHANGE             = "TRUE",
            p_RXBUFRESET_TIME                        = 0b00001,
            p_RXBUF_THRESH_OVFLW                     = 61,
            p_RXBUF_THRESH_OVRD                      = "FALSE",
            p_RXBUF_THRESH_UNDFLW                    = 4,
            p_RXDLY_CFG                              = 0x001F,
            p_RXDLY_LCFG                             = 0x030,
            p_RXDLY_TAP_CFG                          = 0x0000,
            p_RXPH_CFG                               = 0x000000,
            p_RXPHDLY_CFG                            = 0x084020,
            p_RXPH_MONITOR_SEL                       = 0b00000,
            p_RX_XCLK_SEL                            = "RXREC",
            p_RX_DDI_SEL                             = 0b000000,
            p_RX_DEFER_RESET_BUF_EN                  = "TRUE",

            # CDR Attributes
            p_RXCDR_CFG                              = 0x03000023ff10100020,
            p_RXCDR_FR_RESET_ON_EIDLE                = 0b0,
            p_RXCDR_HOLD_DURING_EIDLE                = 0b0,
            p_RXCDR_PH_RESET_ON_EIDLE                = 0b0,
            p_RXCDR_LOCK_CFG                         = 0b010101,

            # RX Initialization and Reset Attributes
            p_RXCDRFREQRESET_TIME                    = 0b00001,
            p_RXCDRPHRESET_TIME                      = 0b00001,
            p_RXISCANRESET_TIME                      = 0b00001,
            p_RXPCSRESET_TIME                        = 0b00001,
            p_RXPMARESET_TIME                        = 0b00011,

            # RX OOB Signaling Attributes
            p_RXOOB_CFG                              = 0b0000110,

            # RX Gearbox Attributes
            p_RXGEARBOX_EN                           = "FALSE",
            p_GEARBOX_MODE                           = 0b000,

            # PRBS Detection Attribute
            p_RXPRBS_ERR_LOOPBACK                    = 0b0,

            # Power-Down Attributes
            p_PD_TRANS_TIME_FROM_P2                  = 0x03c,
            p_PD_TRANS_TIME_NONE_P2                  = 0x3c,
            p_PD_TRANS_TIME_TO_P2                    = 0x64,

            # RX OOB Signaling Attributes
            p_SAS_MAX_COM                            = 64,
            p_SAS_MIN_COM                            = 36,
            p_SATA_BURST_SEQ_LEN                     = 0b0101,
            p_SATA_BURST_VAL                         = 0b100,
            p_SATA_EIDLE_VAL                         = 0b100,
            p_SATA_MAX_BURST                         = 8,
            p_SATA_MAX_INIT                          = 21,
            p_SATA_MAX_WAKE                          = 7,
            p_SATA_MIN_BURST                         = 4,
            p_SATA_MIN_INIT                          = 12,
            p_SATA_MIN_WAKE                          = 4,

            # RX Fabric Clock Output Control Attributes
            p_TRANS_TIME_RATE                        = 0x0E,

            # TX Buffer Attributes
            p_TXBUF_EN                               = "TRUE",
            p_TXBUF_RESET_ON_RATE_CHANGE             = "TRUE",
            p_TXDLY_CFG                              = 0x001F,
            p_TXDLY_LCFG                             = 0x030,
            p_TXDLY_TAP_CFG                          = 0x0000,
            p_TXPH_CFG                               = 0x0780,
            p_TXPHDLY_CFG                            = 0x084020,
            p_TXPH_MONITOR_SEL                       = 0b00000,
            p_TX_XCLK_SEL                            = "TXOUT",

            # FPGA TX Interface Attributes
            p_TX_DATA_WIDTH                          = 20,

            # TX Configurable Driver Attributes
            p_TX_DEEMPH0                             = 0b00000,
            p_TX_DEEMPH1                             = 0b00000,
            p_TX_EIDLE_ASSERT_DELAY                  = 0b110,
            p_TX_EIDLE_DEASSERT_DELAY                = 0b100,
            p_TX_LOOPBACK_DRIVE_HIZ                  = "FALSE",
            p_TX_MAINCURSOR_SEL                      = 0b0,
            p_TX_DRIVE_MODE                          = "DIRECT",
            p_TX_MARGIN_FULL_0                       = 0b1001110,
            p_TX_MARGIN_FULL_1                       = 0b1001001,
            p_TX_MARGIN_FULL_2                       = 0b1000101,
            p_TX_MARGIN_FULL_3                       = 0b1000010,
            p_TX_MARGIN_FULL_4                       = 0b1000000,
            p_TX_MARGIN_LOW_0                        = 0b1000110,
            p_TX_MARGIN_LOW_1                        = 0b1000100,
            p_TX_MARGIN_LOW_2                        = 0b1000010,
            p_TX_MARGIN_LOW_3                        = 0b1000000,
            p_TX_MARGIN_LOW_4                        = 0b1000000,

            # TX Gearbox Attributes
            p_TXGEARBOX_EN                           = "FALSE",

            # TX Initialization and Reset Attributes
            p_TXPCSRESET_TIME                        = 0b00001,
            p_TXPMARESET_TIME                        = 0b00001,

            # TX Receiver Detection Attributes
            p_TX_RXDETECT_CFG                        = 0x1832,
            p_TX_RXDETECT_REF                        = 0b100,

            # CPLL Attributes
            p_CPLL_CFG                               = 0xBC07DC,
            p_CPLL_FBDIV                             = pll.config["n2"],
            p_CPLL_FBDIV_45                          = pll.config["n1"],
            p_CPLL_INIT_CFG                          = 0x00001E,
            p_CPLL_LOCK_CFG                          = 0x01E8,
            p_CPLL_REFCLK_DIV                        = pll.config["m"],
            p_RXOUT_DIV                              = pll.config["d"],
            p_TXOUT_DIV                              = pll.config["d"],
            p_SATA_CPLL_CFG                          = "VCO_3000MHZ",

            # RX Initialization and Reset Attributes
            p_RXDFELPMRESET_TIME                     = 0b0001111,

            # RX Equalizer Attributes
            p_RXLPM_HF_CFG                           = 0b00000011110000,
            p_RXLPM_LF_CFG                           = 0b00000011110000,
            p_RX_DFE_GAIN_CFG                        = 0x020FEA,
            p_RX_DFE_H2_CFG                          = 0b000000000000,
            p_RX_DFE_H3_CFG                          = 0b000001000000,
            p_RX_DFE_H4_CFG                          = 0b00011110000,
            p_RX_DFE_H5_CFG                          = 0b00011100000,
            p_RX_DFE_KL_CFG                          = 0b0000011111110,
            p_RX_DFE_LPM_CFG                         = 0x0954,
            p_RX_DFE_LPM_HOLD_DURING_EIDLE           = 0b0,
            p_RX_DFE_UT_CFG                          = 0b10001111000000000,
            p_RX_DFE_VP_CFG                          = 0b00011111100000011,

            # Power-Down Attributes
            p_RX_CLKMUX_PD                           = 0b1,
            p_TX_CLKMUX_PD                           = 0b1,

            # FPGA RX Interface Attribute
            p_RX_INT_DATAWIDTH                       = 0,

            # FPGA TX Interface Attribute
            p_TX_INT_DATAWIDTH                       = 0,

            # TX Configurable Driver Attributes
            p_TX_QPI_STATUS_EN                       = 0b0,

            # RX Equalizer Attributes
            p_RX_DFE_KL_CFG2                         = 0x301148AC,
            p_RX_DFE_XYD_CFG                         = 0b0000000000000,

            # TX Configurable Driver Attributes
            p_TX_PREDRIVER_MODE                      = 0b0
        )
        gtx_params.update(
            # CPLL Ports
            o_CPLLFBCLKLOST                  = Open(),
            o_CPLLLOCK                       = pll.lock,
            i_CPLLLOCKDETCLK                 = ClockSignal(),
            i_CPLLLOCKEN                     = 1,
            i_CPLLPD                         = 0,
            o_CPLLREFCLKLOST                 = Open(),
            i_CPLLREFCLKSEL                  = 0b001,
            i_CPLLRESET                      = pll.reset,
            i_GTRSVD                         = 0b0000000000000000,
            i_PCSRSVDIN                      = 0b0000000000000000,
            i_PCSRSVDIN2                     = 0b00000,
            i_PMARSVDIN                      = 0b00000,
            i_PMARSVDIN2                     = 0b00000,
            i_TSTIN                          = 0b11111111111111111111,
            o_TSTOUT                         = Open(),

            # Channel
            i_CLKRSVD                        = 0b0000,

            # Channel - Clocking Ports
            i_GTGREFCLK                      = 0,
            i_GTNORTHREFCLK0                 = 0,
            i_GTNORTHREFCLK1                 = 0,
            i_GTREFCLK0                      = pll.refclk,
            i_GTREFCLK1                      = 0,
            i_GTSOUTHREFCLK0                 = 0,
            i_GTSOUTHREFCLK1                 = 0,

            # Channel - DRP Ports
            i_DRPADDR                        = 0,
            i_DRPCLK                         = 0,
            i_DRPDI                          = 0,
            o_DRPDO                          = Open(),
            i_DRPEN                          = 0,
            o_DRPRDY                         = Open(),
            i_DRPWE                          = 0,

            # Clocking Ports
            o_GTREFCLKMONITOR                = Open(),
            i_QPLLCLK                        = 0,
            i_QPLLREFCLK                     = 0,
            i_RXSYSCLKSEL                    = 0b00,
            i_TXSYSCLKSEL                    = 0b00,

            # Digital Monitor Ports
            o_DMONITOROUT                    = Open(),

            # FPGA TX Interface Datapath Configuration
            i_TX8B10BEN                      = 0,

            # Loopback Ports
            i_LOOPBACK                       = 0b000,

            # PCI Express Ports
            o_PHYSTATUS                      = Open(),
            i_RXRATE                         = 0b000,
            o_RXVALID                        = Open(),

            # Power-Down Ports
            i_RXPD                           = 0b00,
            i_TXPD                           = 0b00,

            # RX 8B/10B Decoder Ports
            i_SETERRSTATUS                   = 0,

            # RX Initialization and Reset Ports
            i_EYESCANRESET                   = 0,
            i_RXUSERRDY                      = rx_mmcm_locked,

            # RX Margin Analysis Ports
            o_EYESCANDATAERROR               = Open(),
            i_EYESCANMODE                    = 0,
            i_EYESCANTRIGGER                 = 0,

            # Receive Ports - CDR Ports
            i_RXCDRFREQRESET                 = 0,
            i_RXCDRHOLD                      = 0,
            o_RXCDRLOCK                      = Open(),
            i_RXCDROVRDEN                    = 0,
            i_RXCDRRESET                     = 0,
            i_RXCDRRESETRSV                  = 0,

            # Receive Ports - Clock Correction Ports
            o_RXCLKCORCNT                    = Open(),

            # Receive Ports - FPGA RX Interface Datapath Configuration
            i_RX8B10BEN                      = 0,

            # Receive Ports - FPGA RX Interface Ports
            i_RXUSRCLK                       = ClockSignal("eth_rx_half"),
            i_RXUSRCLK2                      = ClockSignal("eth_rx_half"),

            # Receive Ports - FPGA RX interface Ports
            o_RXDATA                         = Cat(*[rx_data[10*i:10*i+8] for i in range(2)]),

            # Receive Ports - Pattern Checker Ports
            o_RXPRBSERR                      = Open(),
            i_RXPRBSSEL                      = 0b000,

            # Receive Ports - Pattern Checker ports
            i_RXPRBSCNTRESET                 = 0,

            # Receive Ports - RX  Equalizer Ports
            i_RXDFEXYDEN                     = 1,
            i_RXDFEXYDHOLD                   = 0,
            i_RXDFEXYDOVRDEN                 = 0,

            # Receive Ports - RX 8B/10B Decoder Ports
            i_RXDISPERR                      = Cat(*[rx_data[10*i+9] for i in range(2)]),
            o_RXNOTINTABLE                   = Open(),

            # Receive Ports - RX AFE
            i_GTXRXP                         = data_pads.rxp,
            # Receive Ports - RX AFE Ports
            i_GTXRXN                         = data_pads.rxn,

            # Receive Ports - RX Buffer Bypass Ports
            i_RXBUFRESET                     = 0,
            o_RXBUFSTATUS                    = Open(),
            i_RXDDIEN                        = 0,
            i_RXDLYBYPASS                    = 1,
            i_RXDLYEN                        = 0,
            i_RXDLYOVRDEN                    = 0,
            i_RXDLYSRESET                    = 0,
            o_RXDLYSRESETDONE                = Open(),
            i_RXPHALIGN                      = 0,
            o_RXPHALIGNDONE                  = Open(),
            i_RXPHALIGNEN                    = 0,
            i_RXPHDLYPD                      = 0,
            i_RXPHDLYRESET                   = 0,
            o_RXPHMONITOR                    = Open(),
            i_RXPHOVRDEN                     = 0,
            o_RXPHSLIPMONITOR                = Open(),
            o_RXSTATUS                       = Open(),

            # Receive Ports - RX Byte and Word Alignment Ports
            o_RXBYTEISALIGNED                = Open(),
            o_RXBYTEREALIGN                  = Open(),
            o_RXCOMMADET                     = Open(),
            i_RXCOMMADETEN                   = 1,
            i_RXMCOMMAALIGNEN                = 1,
            i_RXPCOMMAALIGNEN                = 1,

            # Receive Ports - RX Channel Bonding Ports
            o_RXCHANBONDSEQ                  = Open(),
            i_RXCHBONDEN                     = 0,
            i_RXCHBONDLEVEL                  = 0b000,
            i_RXCHBONDMASTER                 = 0,
            o_RXCHBONDO                      = Open(),
            i_RXCHBONDSLAVE                  = 0,

            # Receive Ports - RX Channel Bonding Ports
            o_RXCHANISALIGNED                = Open(),
            o_RXCHANREALIGN                  = Open(),

            # Receive Ports - RX Equailizer Ports
            i_RXLPMHFHOLD                    = 0,
            i_RXLPMHFOVRDEN                  = 0,
            i_RXLPMLFHOLD                    = 0,

            # Receive Ports - RX Equalizer Ports
            i_RXDFEAGCHOLD                   = 0,
            i_RXDFEAGCOVRDEN                 = 0,
            i_RXDFECM1EN                     = 0,
            i_RXDFELFHOLD                    = 0,
            i_RXDFELFOVRDEN                  = 1,
            i_RXDFELPMRESET                  = 0,
            i_RXDFETAP2HOLD                  = 0,
            i_RXDFETAP2OVRDEN                = 0,
            i_RXDFETAP3HOLD                  = 0,
            i_RXDFETAP3OVRDEN                = 0,
            i_RXDFETAP4HOLD                  = 0,
            i_RXDFETAP4OVRDEN                = 0,
            i_RXDFETAP5HOLD                  = 0,
            i_RXDFETAP5OVRDEN                = 0,
            i_RXDFEUTHOLD                    = 0,
            i_RXDFEUTOVRDEN                  = 0,
            i_RXDFEVPHOLD                    = 0,
            i_RXDFEVPOVRDEN                  = 0,
            i_RXDFEVSEN                      = 0,
            i_RXLPMLFKLOVRDEN                = 0,
            o_RXMONITOROUT                   = Open(),
            i_RXMONITORSEL                   = 0,
            i_RXOSHOLD                       = 0,
            i_RXOSOVRDEN                     = 0,

            # Receive Ports - RX Fabric ClocK Output Control Ports
            o_RXRATEDONE                     = Open(),

            # Receive Ports - RX Fabric Output Control Ports
            o_RXOUTCLK                       = self.rxoutclk,
            o_RXOUTCLKFABRIC                 = Open(),
            o_RXOUTCLKPCS                    = Open(),
            i_RXOUTCLKSEL                    = 0b010,

            # Receive Ports - RX Gearbox Ports
            o_RXDATAVALID                    = Open(),
            o_RXHEADER                       = Open(),
            o_RXHEADERVALID                  = Open(),
            o_RXSTARTOFSEQ                   = Open(),

            # Receive Ports - RX Gearbox Ports
            i_RXGEARBOXSLIP                  = 0,

            # Receive Ports - RX Initialization and Reset Ports
            i_GTRXRESET                      = rx_reset,
            i_RXOOBRESET                     = 0,
            i_RXPCSRESET                     = 0,
            i_RXPMARESET                     = 0,

            # Receive Ports - RX Margin Analysis ports
            i_RXLPMEN                        = 0,

            # Receive Ports - RX OOB Signaling ports
            o_RXCOMSASDET                    = Open(),
            o_RXCOMWAKEDET                   = Open(),

            # Receive Ports - RX OOB Signaling ports
            o_RXCOMINITDET                   = Open(),

            # Receive Ports - RX OOB signalling Ports
            o_RXELECIDLE                     = Open(),
            i_RXELECIDLEMODE                 = 0b11,

            # Receive Ports - RX Polarity Control Ports
            i_RXPOLARITY                     = 0,

            # Receive Ports - RX gearbox ports
            i_RXSLIDE                        = 0,

            # Receive Ports - RX8B/10B Decoder Ports
            o_RXCHARISCOMMA                  = Open(),
            o_RXCHARISK                      = Cat(*[rx_data[10*i+8] for i in range(2)]),

            # Receive Ports - Rx Channel Bonding Ports
            i_RXCHBONDI                      = 0b00000,

            # Receive Ports -RX Initialization and Reset Ports
            o_RXRESETDONE                    = rx_reset_done,

            # Rx AFE Ports
            i_RXQPIEN                        = 0,
            o_RXQPISENN                      = Open(),
            o_RXQPISENP                      = Open(),

            # TX Buffer Bypass Ports
            i_TXPHDLYTSTCLK                  = 0,

            # TX Configurable Driver Ports
            i_TXPOSTCURSOR                   = 0b00000,
            i_TXPOSTCURSORINV                = 0,
            i_TXPRECURSOR                    = 0b00000,
            i_TXPRECURSORINV                 = 0,
            i_TXQPIBIASEN                    = 0,
            i_TXQPISTRONGPDOWN               = 0,
            i_TXQPIWEAKPUP                   = 0,

            # TX Initialization and Reset Ports
            i_CFGRESET                       = 0,
            i_GTTXRESET                      = tx_reset,
            o_PCSRSVDOUT                     = Open(),
            i_TXUSERRDY                      = tx_mmcm_locked,

            # Transceiver Reset Mode Operation
            i_GTRESETSEL                     = 0,
            i_RESETOVRD                      = 0,

            # Transmit Ports - 8b10b Encoder Control Ports
            i_TXCHARDISPMODE                 = Cat(*[tx_data[10*i+9] for i in range(2)]),
            i_TXCHARDISPVAL                  = Cat(*[tx_data[10*i+8] for i in range(2)]),

            # Transmit Ports - FPGA TX Interface Ports
            i_TXUSRCLK                       = ClockSignal("eth_tx_half"),
            i_TXUSRCLK2                      = ClockSignal("eth_tx_half"),

            # Transmit Ports - PCI Express Ports
            i_TXELECIDLE                     = 0,
            i_TXMARGIN                       = 0b000,
            i_TXRATE                         = 0b000,
            i_TXSWING                        = 0,

            # Transmit Ports - Pattern Generator Ports
            i_TXPRBSFORCEERR                 = 0,

            # Transmit Ports - TX Buffer Bypass Ports
            i_TXDLYBYPASS                    = 1,
            i_TXDLYEN                        = 0,
            i_TXDLYHOLD                      = 0,
            i_TXDLYOVRDEN                    = 0,
            i_TXDLYSRESET                    = 0,
            o_TXDLYSRESETDONE                = Open(),
            i_TXDLYUPDOWN                    = 0,
            i_TXPHALIGN                      = 0,
            o_TXPHALIGNDONE                  = Open(),
            i_TXPHALIGNEN                    = 0,
            i_TXPHDLYPD                      = 0,
            i_TXPHDLYRESET                   = 0,
            i_TXPHINIT                       = 0,
            o_TXPHINITDONE                   = Open(),
            i_TXPHOVRDEN                     = 0,

            # Transmit Ports - TX Buffer Ports
            o_TXBUFSTATUS                    = Open(),

            # Transmit Ports - TX Configurable Driver Ports
            i_TXBUFDIFFCTRL                  = 0b100,
            i_TXDEEMPH                       = 0,
            i_TXDIFFCTRL                     = 0b1000,
            i_TXDIFFPD                       = 0,
            i_TXINHIBIT                      = 0,
            i_TXMAINCURSOR                   = 0b0000000,
            i_TXPISOPD                       = 0,

            # Transmit Ports - TX Data Path interface
            i_TXDATA                         = Cat(*[tx_data[10*i:10*i+8] for i in range(2)]),

            # Transmit Ports - TX Driver and OOB signaling
            o_GTXTXN                         = data_pads.txn,
            o_GTXTXP                         = data_pads.txp,

            # Transmit Ports - TX Fabric Clock Output Control Ports
            o_TXOUTCLK                       = self.txoutclk,
            o_TXOUTCLKFABRIC                 = Open(),
            o_TXOUTCLKPCS                    = Open(),
            i_TXOUTCLKSEL                    = 0b010,
            o_TXRATEDONE                     = Open(),

            # Transmit Ports - TX Gearbox Ports
            i_TXCHARISK                      = 0b00000000,
            o_TXGEARBOXREADY                 = Open(),
            i_TXHEADER                       = 0b000,
            i_TXSEQUENCE                     = 0b0000000,
            i_TXSTARTSEQ                     = 0,

            # Transmit Ports - TX Initialization and Reset Ports
            i_TXPCSRESET                     = 0,
            i_TXPMARESET                     = 0,
            o_TXRESETDONE                    = tx_reset_done,

            # Transmit Ports - TX OOB signaling Ports
            o_TXCOMFINISH                    = Open(),
            i_TXCOMINIT                      = 0,
            i_TXCOMSAS                       = 0,
            i_TXCOMWAKE                      = 0,
            i_TXPDELECIDLEMODE               = 0,

            # Transmit Ports - TX Polarity Control Ports
            i_TXPOLARITY                     = 0,

            # Transmit Ports - TX Receiver Detection Ports
            i_TXDETECTRX                     = 0,

            # Transmit Ports - TX8b/10b Encoder Ports
            i_TX8B10BBYPASS                  = 0b00000000,

            # Transmit Ports - pattern Generator Ports
            i_TXPRBSSEL                      = 0b000,

            # Tx Configurable Driver  Ports
            o_TXQPISENN                      = Open(),
            o_TXQPISENP                      = Open(),
        )
        self.specials += Instance("GTXE2_CHANNEL", **gtx_params)

        # Get 125MHz clocks back - the GTX is outputting 62.5MHz.
        txoutclk_rebuffer = Signal()
        self.specials += Instance("BUFH",
            i_I = self.txoutclk,
            o_O = txoutclk_rebuffer
        )
        rxoutclk_rebuffer = Signal()
        self.specials += Instance("BUFG",
            i_I = self.rxoutclk,
            o_O = rxoutclk_rebuffer
        )

        tx_mmcm_fb        = Signal()
        tx_mmcm_reset     = Signal(reset=1)
        clk_tx_unbuf      = Signal()
        clk_tx_half_unbuf = Signal()
        self.specials += [
            Instance("MMCME2_BASE",
                p_CLKIN1_PERIOD    = 16.0,
                i_CLKIN1           = txoutclk_rebuffer,
                i_RST              = tx_mmcm_reset,

                o_CLKFBOUT         = tx_mmcm_fb,
                i_CLKFBIN          = tx_mmcm_fb,

                p_CLKFBOUT_MULT_F  = 16,
                o_LOCKED           = tx_mmcm_locked,
                p_DIVCLK_DIVIDE    = 1,

                p_CLKOUT0_DIVIDE_F = 16,
                o_CLKOUT0          = clk_tx_half_unbuf,
                p_CLKOUT1_DIVIDE   = 8,
                o_CLKOUT1          = clk_tx_unbuf,
            ),
            Instance("BUFH",
                i_I = clk_tx_half_unbuf,
                o_O = self.cd_eth_tx_half.clk,
            ),
            Instance("BUFH",
                i_I = clk_tx_unbuf,
                o_O = self.cd_eth_tx.clk,
            ),
            AsyncResetSynchronizer(self.cd_eth_tx, ~tx_mmcm_locked)
        ]

        rx_mmcm_fb        = Signal()
        rx_mmcm_reset     = Signal(reset=1)
        clk_rx_unbuf      = Signal()
        clk_rx_half_unbuf = Signal()
        self.specials += [
            Instance("MMCME2_BASE",
                p_CLKIN1_PERIOD    = 16.0,
                i_CLKIN1           = rxoutclk_rebuffer,
                i_RST              = rx_mmcm_reset,

                o_CLKFBOUT         = rx_mmcm_fb,
                i_CLKFBIN          = rx_mmcm_fb,

                p_CLKFBOUT_MULT_F  = 16,
                o_LOCKED           = rx_mmcm_locked,
                p_DIVCLK_DIVIDE    = 1,

                p_CLKOUT0_DIVIDE_F = 16,
                o_CLKOUT0          = clk_rx_half_unbuf,
                p_CLKOUT1_DIVIDE   = 8,
                o_CLKOUT1          = clk_rx_unbuf,
            ),
            Instance("BUFG",
                i_I = clk_rx_half_unbuf,
                o_O = self.cd_eth_rx_half.clk,
            ),
            Instance("BUFG",
                i_I = clk_rx_unbuf,
                o_O = self.cd_eth_rx.clk,
            ),
            AsyncResetSynchronizer(self.cd_eth_rx, ~rx_mmcm_locked)
        ]

        # Transceiver init
        tx_init = GTXTXInit(sys_clk_freq, buffer_enable=True)
        self.comb += [
            pll.reset.eq(tx_init.pllreset),
            tx_init.plllock.eq(pll.lock),
            tx_reset.eq(tx_init.gtXxreset)
        ]
        self.sync += tx_mmcm_reset.eq(~pll.lock)
        tx_mmcm_reset.attr.add("no_retiming")


        rx_init = ResetInserter()(GTXRXInit(sys_clk_freq, buffer_enable=True))
        self.submodules += rx_init
        self.comb += [
            rx_init.reset.eq(~tx_init.done),
            rx_reset.eq(rx_init.gtXxreset)
        ]
        ps_restart = PulseSynchronizer("eth_tx", "sys")
        self.submodules += ps_restart
        self.comb += [
            ps_restart.i.eq(pcs.restart),
            rx_init.restart.eq(ps_restart.o)
        ]

        # Assume CDR lock time is 50,000 UI as per DS183 and similar to what the Xilinx wizards does.
        cdr_lock_time    = round(sys_clk_freq*50e3/1.25e9)
        cdr_lock_counter = Signal(max=cdr_lock_time+1)
        cdr_locked       = Signal()
        self.sync += [
            If(rx_reset,
                cdr_locked.eq(0),
                cdr_lock_counter.eq(0)
            ).Elif(cdr_lock_counter != cdr_lock_time,
                cdr_lock_counter.eq(cdr_lock_counter + 1)
            ).Else(
                cdr_locked.eq(1)
            ),
            rx_mmcm_reset.eq(~cdr_locked)
        ]
        rx_mmcm_reset.attr.add("no_retiming")

        # Gearbox and PCS connection
        gearbox = Gearbox()
        self.submodules += gearbox

        self.comb += [
            tx_data.eq(gearbox.tx_data_half),
            gearbox.rx_data_half.eq(rx_data),

            gearbox.tx_data.eq(pcs.tbi_tx),
            pcs.tbi_rx.eq(gearbox.rx_data)
        ]
