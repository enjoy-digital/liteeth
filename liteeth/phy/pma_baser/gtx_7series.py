#
# This file is part of LiteEth.
#
# Ported from phy/k7_gtx_10g_baser.py on the liteeth-a7-5000baser-working branch, where the same
# transceiver drove a vendored Verilog PCS. The transceiver half is split out here so it can sit
# under the pure-LiteX PCS in phy/pcs_baser, alongside the UltraScale+ PMAs in this package. The
# GTXE2_CHANNEL parameters and the MMCM/reset sequencing are carried over unchanged: they are what
# was brought up on hardware at 5G and 10G.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

import math

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer, MultiReg

from litex.gen import *

from litex.soc.cores.clock import S7MMCM

# Kintex-7 GTX BASE-R PMA --------------------------------------------------------------------------

class PMA_K7_GTX_10G_BASER(LiteXModule):
    """10GBASE-R PMA on a Kintex-7 GTX, driven from a shared GTXQuadPLL.

    Presents the data interface the UltraScale+ PMAs in this package present -- 64-bit data and a
    2-bit sync header each way, plus a receive bitslip -- with one addition the 7-series needs:
    tx_ce/rx_ce.

    The 7-series internal 64b/66b gearbox is gapped where the GTY's is not. On transmit the fabric
    owns the sequence counter, and 33 cycles carry 32 blocks (33*64 = 2112 = 32*66), so one cycle
    in 33 is a pause. On receive RXDATAVALID marks the cycles that actually carry a block. The PCS
    above must therefore be advanced only on those cycles; K7_GTX_10G_BASER does that by wrapping
    it in CEInserter(["eth_tx", "eth_rx"]) and driving its per-domain enables from tx_ce/rx_ce.
    """
    linerate    = 10.3125e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64

    def __init__(self, qpll, data_pads, sys_clk_freq, tx_polarity=0, rx_polarity=0):
        from liteiclink.serdes.gtx_7series import GTXTXInit, GTXRXInit

        # Interface to the PCS.
        self.tx_data   = Signal(64)
        self.tx_header = Signal(2)
        self.rx_data   = Signal(64)
        self.rx_header = Signal(2)
        self.rx_slip   = Signal()

        # Gearbox cadence. High on the cycles the PCS may advance on.
        self.tx_ce = Signal()
        self.rx_ce = Signal()

        # Receive restart request from the PCS (its serdes_rx_reset_req), in eth_rx. Pulsing this
        # re-runs the RX init sequence, which is how the PCS asks for a fresh CDR lock when block
        # sync cannot be reached.
        self.rx_reset_req = Signal()

        self.reset    = Signal()
        self.loopback = Signal(3)

        # Exposed for clock constraints.
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        # Diagnostics.
        self.tx_reset_done  = Signal()
        self.rx_reset_done  = Signal()
        self.tx_mmcm_locked = Signal()
        self.rx_mmcm_locked = Signal()

        # TX driver controls, exposed so they can be swept at runtime from a CSR.
        self.tx_diffctrl   = Signal(4, reset=0b1000)
        self.tx_precursor  = Signal(5, reset=0)
        self.tx_postcursor = Signal(5, reset=0)

        # The transceiver's user clocks. The PHY above aliases these rather than creating its own.
        self.cd_eth_tx     = ClockDomain()
        self.cd_eth_rx     = ClockDomain()
        self.cd_eth_tx_usr = ClockDomain(reset_less=True)
        self.cd_eth_rx_usr = ClockDomain(reset_less=True)
        self.cd_eth_tx_raw = ClockDomain(reset_less=True)
        self.cd_eth_rx_raw = ClockDomain(reset_less=True)

        # # #

        # Transceiver signals. Names match the ported GTXE2_CHANNEL parameter block.
        tx_reset        = Signal()
        tx_mmcm_locked  = Signal()
        tx_mmcm_reset   = Signal(reset=1)
        tx_data         = Signal(64)
        tx_header       = Signal(3)
        tx_sequence     = Signal(7)
        tx_reset_done   = self.tx_reset_done

        rx_reset        = Signal()
        rx_mmcm_locked  = Signal()
        rx_mmcm_reset   = Signal(reset=1)
        rx_data         = Signal(64)
        rx_header       = Signal(3)
        rx_header_valid = Signal()
        rx_data_valid   = Signal(2)
        rx_reset_done   = self.rx_reset_done

        # Set once the transmitter is up with its PMA dividers re-initialised; see tx_pma_auto.
        self.tx_pma_done      = Signal()
        tx_pma_auto           = Signal()

        # TX gearbox sequencing. With the external sequence counter the fabric owns the pause:
        # TXSEQUENCE 0..31 present payload and 32 is the pause. TXGEARBOXREADY is not a
        # flow-control input in this mode.
        #
        # The counter is held at 0 until TXRESETDONE rather than free-running from the eth_tx reset.
        # That reset releases on tx_mmcm.locked, which also drives TXUSERRDY, so a free-running
        # counter starts before the transmitter is ready; holding TXSEQUENCE at 0 until then is the
        # start-up order UG476 asks for. It was first added on the belief that it was why the
        # transmit path came up unusable on about half of all resets. It was not: the failure rate
        # was unchanged with it (6/10 bad against 5/10), and the cause was the TX PMA dividers --
        # see tx_pma_auto. It stays because it is the documented order, not because it was measured
        # to matter.
        tx_gearbox_ready = Signal()
        tx_gearbox_start = Signal()
        tx_gearbox_wait  = Signal(8)
        self.specials += MultiReg(tx_reset_done, tx_gearbox_ready, "eth_tx")
        self.sync.eth_tx += [
            If(~tx_gearbox_ready,
                tx_gearbox_wait.eq(0),
                tx_gearbox_start.eq(0),
            ).Elif(tx_gearbox_wait != 0xff,
                tx_gearbox_wait.eq(tx_gearbox_wait + 1),
            ).Else(
                tx_gearbox_start.eq(1),
            ),

            If(~tx_gearbox_start,
                tx_sequence.eq(0),
            ).Elif(tx_sequence == 32,
                tx_sequence.eq(0),
            ).Else(
                tx_sequence.eq(tx_sequence + 1),
            ),
        ]
        self.comb += [
            # Nothing may be handed to the gearbox before it is sequencing, so the PCS is stalled
            # by the same signal rather than by the pause cycle alone.
            self.tx_ce.eq(tx_gearbox_start & (tx_sequence != 32)),
            tx_data.eq(self.tx_data),
            tx_header.eq(Cat(self.tx_header, 0)),
        ]

        # RX: one 64-bit block per RXUSRCLK2 cycle that RXDATAVALID marks.
        self.comb += [
            self.rx_ce.eq(rx_data_valid[0]),
            self.rx_data.eq(rx_data),
            self.rx_header.eq(rx_header[:2]),
        ]

        # UG476: RXGEARBOXSLIP must be asserted for exactly one RXUSRCLK2 cycle and then held low
        # for at least 32 cycles while the gearbox settles. Hold off for 64 after each accepted
        # slip.
        SLIP_HOLDOFF  = 64
        rx_slip_d     = Signal()
        rx_slip_pulse = Signal()
        rx_slip_wait  = Signal(max=SLIP_HOLDOFF + 1)
        self.sync.eth_rx += [
            rx_slip_d.eq(self.rx_slip),
            If(rx_slip_wait != 0,
                rx_slip_wait.eq(rx_slip_wait - 1),
            ).Elif(self.rx_slip & ~rx_slip_d,
                rx_slip_wait.eq(SLIP_HOLDOFF),
            ),
        ]
        self.comb += rx_slip_pulse.eq((rx_slip_wait == 0) & self.rx_slip & ~rx_slip_d)

        # Exposed for probing: the gearbox sequence the GTX is being handed, and what goes with it.
        self.tx_sequence     = tx_sequence
        self.gtx_tx_data     = tx_data
        self.gtx_tx_header   = tx_header
        self.rx_data_valid   = rx_data_valid
        self.rx_header_valid = rx_header_valid
        self.gtx_rx_header   = rx_header      # Raw 3-bit RXHEADER, before the PCS sees 2 bits of it.
        self.rx_slip_pulse   = rx_slip_pulse  # What actually reaches RXGEARBOXSLIP, after holdoff.

        # Divides the reference clock to at most 25MHz for internal calibration.
        clk25_div = math.ceil(qpll.config["clkin"]/25e6)

        gtx_params = dict(
            # Simulation-Only Attributes
            p_SIM_RECEIVER_DETECT_PASS     = "TRUE",
            p_SIM_TX_EIDLE_DRIVE_LEVEL     = "X",
            p_SIM_RESET_SPEEDUP            = "FALSE",
            p_SIM_CPLLREFCLK_SEL           = "FALSE",
            p_SIM_VERSION                  = "4.0",

            # RX Byte and Word Alignment Attributes
            p_ALIGN_COMMA_DOUBLE           = "FALSE",
            p_ALIGN_COMMA_ENABLE           = 0b1111111111,
            p_ALIGN_COMMA_WORD             = 2,
            p_ALIGN_MCOMMA_DET             = "TRUE",
            p_ALIGN_MCOMMA_VALUE           = 0b1010000011,
            p_ALIGN_PCOMMA_DET             = "TRUE",
            p_ALIGN_PCOMMA_VALUE           = 0b0101111100,
            p_SHOW_REALIGN_COMMA           = "TRUE",
            p_RXSLIDE_AUTO_WAIT            = 7,
            p_RXSLIDE_MODE                 = "OFF",
            p_RX_SIG_VALID_DLY             = 10,

            # RX 8B/10B Decoder Attributes
            p_RX_DISPERR_SEQ_MATCH         = "TRUE",
            p_DEC_MCOMMA_DETECT            = "TRUE",
            p_DEC_PCOMMA_DETECT            = "TRUE",
            p_DEC_VALID_COMMA_ONLY         = "TRUE",

            # RX Clock Correction Attributes
            p_CBCC_DATA_SOURCE_SEL         = "DECODED",
            p_CLK_COR_SEQ_2_USE            = "FALSE",
            p_CLK_COR_KEEP_IDLE            = "FALSE",
            p_CLK_COR_MAX_LAT              = 9,
            p_CLK_COR_MIN_LAT              = 7,
            p_CLK_COR_PRECEDENCE           = "TRUE",
            p_CLK_COR_REPEAT_WAIT          = 0,
            p_CLK_COR_SEQ_LEN              = 1,
            p_CLK_COR_SEQ_1_ENABLE         = 0b1111,
            p_CLK_COR_SEQ_1_1              = 0b0100000000,
            p_CLK_COR_SEQ_1_2              = 0b0000000000,
            p_CLK_COR_SEQ_1_3              = 0b0000000000,
            p_CLK_COR_SEQ_1_4              = 0b0000000000,
            p_CLK_CORRECT_USE              = "FALSE",
            p_CLK_COR_SEQ_2_ENABLE         = 0b1111,
            p_CLK_COR_SEQ_2_1              = 0b0100000000,
            p_CLK_COR_SEQ_2_2              = 0b0000000000,
            p_CLK_COR_SEQ_2_3              = 0b0000000000,
            p_CLK_COR_SEQ_2_4              = 0b0000000000,

            # RX Channel Bonding Attributes
            p_CHAN_BOND_KEEP_ALIGN         = "FALSE",
            p_CHAN_BOND_MAX_SKEW           = 1,
            p_CHAN_BOND_SEQ_LEN            = 1,
            p_CHAN_BOND_SEQ_1_1            = 0b0000000000,
            p_CHAN_BOND_SEQ_1_2            = 0b0000000000,
            p_CHAN_BOND_SEQ_1_3            = 0b0000000000,
            p_CHAN_BOND_SEQ_1_4            = 0b0000000000,
            p_CHAN_BOND_SEQ_1_ENABLE       = 0b1111,
            p_CHAN_BOND_SEQ_2_1            = 0b0000000000,
            p_CHAN_BOND_SEQ_2_2            = 0b0000000000,
            p_CHAN_BOND_SEQ_2_3            = 0b0000000000,
            p_CHAN_BOND_SEQ_2_4            = 0b0000000000,
            p_CHAN_BOND_SEQ_2_ENABLE       = 0b1111,
            p_CHAN_BOND_SEQ_2_USE          = "FALSE",
            p_FTS_DESKEW_SEQ_ENABLE        = 0b1111,
            p_FTS_LANE_DESKEW_CFG          = 0b1111,
            p_FTS_LANE_DESKEW_EN           = "FALSE",

            # RX Margin Analysis Attributes
            p_ES_CONTROL                   = 0b000000,
            p_ES_ERRDET_EN                 = "FALSE",
            p_ES_EYE_SCAN_EN               = "TRUE",
            p_ES_HORZ_OFFSET               = 0x000,
            p_ES_PMA_CFG                   = 0b0000000000,
            p_ES_PRESCALE                  = 0b00000,
            p_ES_QUALIFIER                 = 0x00000000000000000000,
            p_ES_QUAL_MASK                 = 0x00000000000000000000,
            p_ES_SDATA_MASK                = 0x00000000000000000000,
            p_ES_VERT_OFFSET               = 0b000000000,

            # FPGA RX Interface Attributes
            p_RX_DATA_WIDTH                = 64,

            # PMA Attributes
            p_OUTREFCLK_SEL_INV            = 0b11,
            p_PMA_RSV                      = 0x001e7080,
            p_PMA_RSV2                     = 0x2050,
            p_PMA_RSV3                     = 0b00,
            p_PMA_RSV4                     = 0x00000000,
            p_RX_BIAS_CFG                  = 0b000000000100,
            p_DMONITOR_CFG                 = 0x000A00,
            p_RX_CM_SEL                    = 0b11,
            p_RX_CM_TRIM                   = 0b010,
            p_RX_DEBUG_CFG                 = 0b000000000000,
            p_RX_OS_CFG                    = 0b0000010000000,
            p_TERM_RCAL_CFG                = 0b10000,
            p_TERM_RCAL_OVRD               = 0b0,
            p_TST_RSV                      = 0x00000000,
            # UG476: these divide the reference clock down to the internal ~25MHz clock that
            # times PMA reset and CDR calibration, so the value tracks the reference and must
            # be ceil(f_refclk / 25MHz).
            p_RX_CLK25_DIV                 = clk25_div,
            p_TX_CLK25_DIV                 = clk25_div,
            p_UCODEER_CLR                  = 0b0,

            # PCI Express Attributes
            p_PCS_PCIE_EN                  = "FALSE",

            # PCS Attributes
            p_PCS_RSVD_ATTR                = 0x000000000000,

            # RX Buffer Attributes
            p_RXBUF_ADDR_MODE              = "FAST",
            p_RXBUF_EIDLE_HI_CNT           = 0b1000,
            p_RXBUF_EIDLE_LO_CNT           = 0b0000,
            p_RXBUF_EN                     = "TRUE",
            p_RX_BUFFER_CFG                = 0b000000,
            p_RXBUF_RESET_ON_CB_CHANGE     = "TRUE",
            p_RXBUF_RESET_ON_COMMAALIGN    = "FALSE",
            p_RXBUF_RESET_ON_EIDLE         = "FALSE",
            p_RXBUF_RESET_ON_RATE_CHANGE   = "TRUE",
            p_RXBUFRESET_TIME              = 0b00001,
            p_RXBUF_THRESH_OVFLW           = 61,
            p_RXBUF_THRESH_OVRD            = "FALSE",
            p_RXBUF_THRESH_UNDFLW          = 4,
            p_RXDLY_CFG                    = 0x001F,
            p_RXDLY_LCFG                   = 0x030,
            p_RXDLY_TAP_CFG                = 0x0000,
            p_RXPH_CFG                     = 0x000000,
            p_RXPHDLY_CFG                  = 0x084020,
            p_RXPH_MONITOR_SEL             = 0b00000,
            p_RX_XCLK_SEL                  = "RXREC",
            p_RX_DDI_SEL                   = 0b000000,
            p_RX_DEFER_RESET_BUF_EN        = "TRUE",

            # CDR Attributes
            # CDR settings are line-rate dependent, keyed by the channel output divider (the
            # same table liteiclink's gtx_7series.py uses).
            p_RXCDR_CFG                    = {
                1 : 0x0b000023ff10400020,
                2 : 0x03000023ff10200020,
                4 : 0x03000023ff10100020,
                8 : 0x03000023ff10080020,
               16 : 0x03000023ff10080020,
            }[qpll.config["d"]],
            p_RXCDR_FR_RESET_ON_EIDLE      = 0b0,
            p_RXCDR_HOLD_DURING_EIDLE      = 0b0,
            p_RXCDR_PH_RESET_ON_EIDLE      = 0b0,
            p_RXCDR_LOCK_CFG               = 0b010101,

            # RX Initialization and Reset Attributes
            p_RXCDRFREQRESET_TIME          = 0b00001,
            p_RXCDRPHRESET_TIME            = 0b00001,
            p_RXISCANRESET_TIME            = 0b00001,
            p_RXPCSRESET_TIME              = 0b00001,
            p_RXPMARESET_TIME              = 0b00011,

            # RX OOB Signaling Attributes
            p_RXOOB_CFG                    = 0b0000110,

            # RX Gearbox Attributes
            p_RXGEARBOX_EN                 = "TRUE",
            p_GEARBOX_MODE                 = 0b001,

            # PRBS Detection Attribute
            p_RXPRBS_ERR_LOOPBACK          = 0b0,

            # Power-Down Attributes
            p_PD_TRANS_TIME_FROM_P2        = 0x03c,
            p_PD_TRANS_TIME_NONE_P2        = 0x3c,
            p_PD_TRANS_TIME_TO_P2          = 0x64,

            # RX OOB Signaling Attributes
            p_SAS_MAX_COM                  = 64,
            p_SAS_MIN_COM                  = 36,
            p_SATA_BURST_SEQ_LEN           = 0b0101,
            p_SATA_BURST_VAL               = 0b100,
            p_SATA_EIDLE_VAL               = 0b100,
            p_SATA_MAX_BURST               = 8,
            p_SATA_MAX_INIT                = 21,
            p_SATA_MAX_WAKE                = 7,
            p_SATA_MIN_BURST               = 4,
            p_SATA_MIN_INIT                = 12,
            p_SATA_MIN_WAKE                = 4,

            # RX Fabric Clock Output Control Attributes
            p_TRANS_TIME_RATE              = 0x0E,

            # TX Buffer Attributes
            p_TXBUF_EN                     = "TRUE",
            p_TXBUF_RESET_ON_RATE_CHANGE   = "TRUE",
            p_TXDLY_CFG                    = 0x001F,
            p_TXDLY_LCFG                   = 0x030,
            p_TXDLY_TAP_CFG                = 0x0000,
            p_TXPH_CFG                     = 0x0780,
            p_TXPHDLY_CFG                  = 0x084020,
            p_TXPH_MONITOR_SEL             = 0b00000,
            p_TX_XCLK_SEL                  = "TXOUT",

            # FPGA TX Interface Attributes
            p_TX_DATA_WIDTH                = 64,

            # TX Configurable Driver Attributes
            p_TX_DEEMPH0                   = 0b00000,
            p_TX_DEEMPH1                   = 0b00000,
            p_TX_EIDLE_ASSERT_DELAY        = 0b110,
            p_TX_EIDLE_DEASSERT_DELAY      = 0b100,
            p_TX_LOOPBACK_DRIVE_HIZ        = "FALSE",
            p_TX_MAINCURSOR_SEL            = 0b0,
            p_TX_DRIVE_MODE                = "DIRECT",
            p_TX_MARGIN_FULL_0             = 0b1001110,
            p_TX_MARGIN_FULL_1             = 0b1001001,
            p_TX_MARGIN_FULL_2             = 0b1000101,
            p_TX_MARGIN_FULL_3             = 0b1000010,
            p_TX_MARGIN_FULL_4             = 0b1000000,
            p_TX_MARGIN_LOW_0              = 0b1000110,
            p_TX_MARGIN_LOW_1              = 0b1000100,
            p_TX_MARGIN_LOW_2              = 0b1000010,
            p_TX_MARGIN_LOW_3              = 0b1000000,
            p_TX_MARGIN_LOW_4              = 0b1000000,

            # TX Gearbox Attributes
            p_TXGEARBOX_EN                 = "TRUE",

            # TX Initialization and Reset Attributes
            p_TXPCSRESET_TIME              = 0b00001,
            p_TXPMARESET_TIME              = 0b00001,

            # TX Receiver Detection Attributes
            p_TX_RXDETECT_CFG              = 0x1832,
            p_TX_RXDETECT_REF              = 0b100,

            # CPLL Attributes
            p_CPLL_CFG                     = 0xBC07DC,
            p_CPLL_FBDIV                   = 4,
            p_CPLL_FBDIV_45                = 5,
            p_CPLL_INIT_CFG                = 0x00001E,
            p_CPLL_LOCK_CFG                = 0x01E8,
            p_CPLL_REFCLK_DIV              = 1,
            p_RXOUT_DIV                    = qpll.config["d"],
            p_TXOUT_DIV                    = qpll.config["d"],
            p_SATA_CPLL_CFG                = "VCO_3000MHZ",

            # RX Initialization and Reset Attributes
            p_RXDFELPMRESET_TIME           = 0b0001111,

            # RX Equalizer Attributes
            p_RXLPM_HF_CFG                 = 0b00000011110000,
            p_RXLPM_LF_CFG                 = 0b00000011110000,
            p_RX_DFE_GAIN_CFG              = 0x020FEA,
            p_RX_DFE_H2_CFG                = 0b000000000000,
            p_RX_DFE_H3_CFG                = 0b000001000000,
            p_RX_DFE_H4_CFG                = 0b00011110000,
            p_RX_DFE_H5_CFG                = 0b00011100000,
            p_RX_DFE_KL_CFG                = 0b0000011111110,
            p_RX_DFE_LPM_CFG               = 0x0954,
            p_RX_DFE_LPM_HOLD_DURING_EIDLE = 0b0,
            p_RX_DFE_UT_CFG                = 0b10001111000000000,
            p_RX_DFE_VP_CFG                = 0b00011111100000011,

            # Power-Down Attributes
            p_RX_CLKMUX_PD                 = 0b1,
            p_TX_CLKMUX_PD                 = 0b1,

            # FPGA RX Interface Attribute
            p_RX_INT_DATAWIDTH             = 1,

            # FPGA TX Interface Attribute
            p_TX_INT_DATAWIDTH             = 1,

            # TX Configurable Driver Attributes
            p_TX_QPI_STATUS_EN             = 0b0,

            # RX Equalizer Attributes
            p_RX_DFE_KL_CFG2               = 0x301148AC,
            p_RX_DFE_XYD_CFG               = 0b0000000000000,

            # TX Configurable Driver Attributes
            p_TX_PREDRIVER_MODE            = 0b0,

            # CPLL Ports
            o_CPLLFBCLKLOST    = Open(),
            o_CPLLLOCK         = Open(),
            i_CPLLLOCKDETCLK   = ClockSignal(),
            i_CPLLLOCKEN       = 1,
            i_CPLLPD           = 0,
            o_CPLLREFCLKLOST   = Open(),
            i_CPLLREFCLKSEL    = 0b001,
            i_CPLLRESET        = 1,
            i_GTRSVD           = 0b0000000000000000,
            i_PCSRSVDIN        = 0b0000000000000000,
            i_PCSRSVDIN2       = 0b00000,
            i_PMARSVDIN        = 0b00000,
            i_PMARSVDIN2       = 0b00000,
            i_TSTIN            = 0b11111111111111111111,
            o_TSTOUT           = Open(),

            # Channel
            i_CLKRSVD          = 0b0000,

            # Channel - Clocking Ports
            i_GTGREFCLK        = 0,
            i_GTNORTHREFCLK0   = 0,
            i_GTNORTHREFCLK1   = 0,
            i_GTREFCLK0        = 0,
            i_GTREFCLK1        = 0,
            i_GTSOUTHREFCLK0   = 0,
            i_GTSOUTHREFCLK1   = 0,

            # Channel - DRP Ports
            i_DRPADDR          = 0,
            i_DRPCLK           = 0,
            i_DRPDI            = 0,
            o_DRPDO            = Open(),
            i_DRPEN            = 0,
            o_DRPRDY           = Open(),
            i_DRPWE            = 0,

            # Clocking Ports
            o_GTREFCLKMONITOR  = Open(),
            i_QPLLCLK          = qpll.clk,
            i_QPLLREFCLK       = qpll.refclk,
            i_RXSYSCLKSEL      = 0b11,
            i_TXSYSCLKSEL      = 0b11,

            # Digital Monitor Ports
            o_DMONITOROUT      = Open(),

            # FPGA TX Interface Datapath Configuration
            i_TX8B10BEN        = 0,

            # Loopback Ports
            i_LOOPBACK         = self.loopback,

            # PCI Express Ports
            o_PHYSTATUS        = Open(),
            i_RXRATE           = 0b000,
            o_RXVALID          = Open(),

            # Power-Down Ports
            i_RXPD             = 0b00,
            i_TXPD             = 0b00,

            # RX 8B/10B Decoder Ports
            i_SETERRSTATUS     = 0,

            # RX Initialization and Reset Ports
            i_EYESCANRESET     = 0,
            i_RXUSERRDY        = rx_mmcm_locked,

            # RX Margin Analysis Ports
            o_EYESCANDATAERROR = Open(),
            i_EYESCANMODE      = 0,
            i_EYESCANTRIGGER   = 0,

            # Receive Ports - CDR Ports
            i_RXCDRFREQRESET   = 0,
            i_RXCDRHOLD        = 0,
            o_RXCDRLOCK        = Open(),
            i_RXCDROVRDEN      = 0,
            i_RXCDRRESET       = 0,
            i_RXCDRRESETRSV    = 0,

            # Receive Ports - Clock Correction Ports
            o_RXCLKCORCNT      = Open(),

            # Receive Ports - FPGA RX Interface Datapath Configuration
            i_RX8B10BEN        = 0,

            # Receive Ports - FPGA RX Interface Ports
            i_RXUSRCLK         = ClockSignal("eth_rx_usr"),
            i_RXUSRCLK2        = ClockSignal("eth_rx"),

            # Receive Ports - FPGA RX interface Ports
            o_RXDATA           = rx_data,

            # Receive Ports - Pattern Checker Ports
            o_RXPRBSERR        = Open(),
            i_RXPRBSSEL        = 0b000,

            # Receive Ports - Pattern Checker ports
            i_RXPRBSCNTRESET   = 0,

            # Receive Ports - RX  Equalizer Ports
            i_RXDFEXYDEN       = 1,
            i_RXDFEXYDHOLD     = 0,
            i_RXDFEXYDOVRDEN   = 0,

            # Receive Ports - RX 8B/10B Decoder Ports
            i_RXDISPERR        = 0,
            o_RXNOTINTABLE     = Open(),

            # Receive Ports - RX AFE
            i_GTXRXP           = data_pads.rxp,
            # Receive Ports - RX AFE Ports
            i_GTXRXN           = data_pads.rxn,

            # Receive Ports - RX Buffer Bypass Ports
            i_RXBUFRESET       = 0,
            o_RXBUFSTATUS      = Open(),
            i_RXDDIEN          = 0,
            i_RXDLYBYPASS      = 1,
            i_RXDLYEN          = 0,
            i_RXDLYOVRDEN      = 0,
            i_RXDLYSRESET      = 0,
            o_RXDLYSRESETDONE  = Open(),
            i_RXPHALIGN        = 0,
            o_RXPHALIGNDONE    = Open(),
            i_RXPHALIGNEN      = 0,
            i_RXPHDLYPD        = 0,
            i_RXPHDLYRESET     = 0,
            o_RXPHMONITOR      = Open(),
            i_RXPHOVRDEN       = 0,
            o_RXPHSLIPMONITOR  = Open(),
            o_RXSTATUS         = Open(),

            # Receive Ports - RX Byte and Word Alignment Ports
            o_RXBYTEISALIGNED  = Open(),
            o_RXBYTEREALIGN    = Open(),
            o_RXCOMMADET       = Open(),
            i_RXCOMMADETEN     = 0b1,
            i_RXMCOMMAALIGNEN  = 0,
            i_RXPCOMMAALIGNEN  = 0,

            # Receive Ports - RX Channel Bonding Ports
            o_RXCHANBONDSEQ    = Open(),
            i_RXCHBONDEN       = 0,
            i_RXCHBONDLEVEL    = 0b000,
            i_RXCHBONDMASTER   = 0,
            o_RXCHBONDO        = Open(),
            i_RXCHBONDSLAVE    = 0,

            # Receive Ports - RX Channel Bonding Ports
            o_RXCHANISALIGNED  = Open(),
            o_RXCHANREALIGN    = Open(),

            # Receive Ports - RX Equailizer Ports
            i_RXLPMHFHOLD      = 0,
            i_RXLPMHFOVRDEN    = 0,
            i_RXLPMLFHOLD      = 0,

            # Receive Ports - RX Equalizer Ports
            i_RXDFEAGCHOLD     = 0,
            i_RXDFEAGCOVRDEN   = 0,
            i_RXDFECM1EN       = 0,
            i_RXDFELFHOLD      = 0,
            i_RXDFELFOVRDEN    = 1,
            i_RXDFELPMRESET    = 0,
            i_RXDFETAP2HOLD    = 0,
            i_RXDFETAP2OVRDEN  = 0,
            i_RXDFETAP3HOLD    = 0,
            i_RXDFETAP3OVRDEN  = 0,
            i_RXDFETAP4HOLD    = 0,
            i_RXDFETAP4OVRDEN  = 0,
            i_RXDFETAP5HOLD    = 0,
            i_RXDFETAP5OVRDEN  = 0,
            i_RXDFEUTHOLD      = 0,
            i_RXDFEUTOVRDEN    = 0,
            i_RXDFEVPHOLD      = 0,
            i_RXDFEVPOVRDEN    = 0,
            i_RXDFEVSEN        = 0,
            i_RXLPMLFKLOVRDEN  = 0,
            o_RXMONITOROUT     = Open(),
            i_RXMONITORSEL     = 0,
            i_RXOSHOLD         = 0,
            i_RXOSOVRDEN       = 0,

            # Receive Ports - RX Fabric ClocK Output Control Ports
            o_RXRATEDONE       = Open(),

            # Receive Ports - RX Fabric Output Control Ports
            o_RXOUTCLK         = self.rxoutclk,
            o_RXOUTCLKFABRIC   = Open(),
            o_RXOUTCLKPCS      = Open(),
            i_RXOUTCLKSEL      = 0b010,

            # Receive Ports - RX Gearbox Ports
            o_RXDATAVALID      = rx_data_valid,
            o_RXHEADER         = rx_header,
            o_RXHEADERVALID    = rx_header_valid,
            o_RXSTARTOFSEQ     = Open(),

            # Receive Ports - RX Gearbox Ports
            i_RXGEARBOXSLIP    = rx_slip_pulse,

            # Receive Ports - RX Initialization and Reset Ports
            i_GTRXRESET        = rx_reset,
            i_RXOOBRESET       = 0,
            i_RXPCSRESET       = 0,
            i_RXPMARESET       = 0,

            # Receive Ports - RX Margin Analysis ports
            i_RXLPMEN          = 0,

            # Receive Ports - RX OOB Signaling ports
            o_RXCOMSASDET      = Open(),
            o_RXCOMWAKEDET     = Open(),

            # Receive Ports - RX OOB Signaling ports
            o_RXCOMINITDET     = Open(),

            # Receive Ports - RX OOB signalling Ports
            o_RXELECIDLE       = Open(),
            i_RXELECIDLEMODE   = 0b11,

            # Receive Ports - RX Polarity Control Ports
            i_RXPOLARITY       = rx_polarity,

            # Receive Ports - RX gearbox ports
            i_RXSLIDE          = 0,

            # Receive Ports - RX8B/10B Decoder Ports
            o_RXCHARISCOMMA    = Open(),
            o_RXCHARISK        = Open(),

            # Receive Ports - Rx Channel Bonding Ports
            i_RXCHBONDI        = 0b00000,

            # Receive Ports -RX Initialization and Reset Ports
            o_RXRESETDONE      = rx_reset_done,

            # Rx AFE Ports
            i_RXQPIEN          = 0,
            o_RXQPISENN        = Open(),
            o_RXQPISENP        = Open(),

            # TX Buffer Bypass Ports
            i_TXPHDLYTSTCLK    = 0,

            # TX Configurable Driver Ports
            i_TXPOSTCURSOR     = self.tx_postcursor,
            i_TXPOSTCURSORINV  = 0,
            i_TXPRECURSOR      = self.tx_precursor,
            i_TXPRECURSORINV   = 0,
            i_TXQPIBIASEN      = 0,
            i_TXQPISTRONGPDOWN = 0,
            i_TXQPIWEAKPUP     = 0,

            # TX Initialization and Reset Ports
            i_CFGRESET         = 0,
            i_GTTXRESET        = tx_reset,
            o_PCSRSVDOUT       = Open(),
            i_TXUSERRDY        = tx_mmcm_locked,

            # Transceiver Reset Mode Operation
            i_GTRESETSEL       = 0,
            i_RESETOVRD        = 0,

            # Transmit Ports - 8b10b Encoder Control Ports
            i_TXCHARDISPMODE   = 0,
            i_TXCHARDISPVAL    = 0,

            # Transmit Ports - FPGA TX Interface Ports
            i_TXUSRCLK         = ClockSignal("eth_tx_usr"),
            i_TXUSRCLK2        = ClockSignal("eth_tx"),

            # Transmit Ports - PCI Express Ports
            i_TXELECIDLE       = 0,
            i_TXMARGIN         = 0b000,
            i_TXRATE           = 0b000,
            i_TXSWING          = 0,

            # Transmit Ports - Pattern Generator Ports
            i_TXPRBSFORCEERR   = 0,

            # Transmit Ports - TX Buffer Bypass Ports
            i_TXDLYBYPASS      = 1,
            i_TXDLYEN          = 0,
            i_TXDLYHOLD        = 0,
            i_TXDLYOVRDEN      = 0,
            i_TXDLYSRESET      = 0,
            o_TXDLYSRESETDONE  = Open(),
            i_TXDLYUPDOWN      = 0,
            i_TXPHALIGN        = 0,
            o_TXPHALIGNDONE    = Open(),
            i_TXPHALIGNEN      = 0,
            i_TXPHDLYPD        = 0,
            i_TXPHDLYRESET     = 0,
            i_TXPHINIT         = 0,
            o_TXPHINITDONE     = Open(),
            i_TXPHOVRDEN       = 0,

            # Transmit Ports - TX Buffer Ports
            o_TXBUFSTATUS      = Open(),

            # Transmit Ports - TX Configurable Driver Ports
            i_TXBUFDIFFCTRL    = 0b100,
            i_TXDEEMPH         = 0,
            i_TXDIFFCTRL       = self.tx_diffctrl,
            i_TXDIFFPD         = 0,
            i_TXINHIBIT        = 0,
            i_TXMAINCURSOR     = 0b0000000,
            i_TXPISOPD         = 0,

            # Transmit Ports - TX Data Path interface
            i_TXDATA           = tx_data,

            # Transmit Ports - TX Driver and OOB signaling
            o_GTXTXN           = data_pads.txn,
            o_GTXTXP           = data_pads.txp,

            # Transmit Ports - TX Fabric Clock Output Control Ports
            o_TXOUTCLK         = self.txoutclk,
            o_TXOUTCLKFABRIC   = Open(),
            o_TXOUTCLKPCS      = Open(),
            i_TXOUTCLKSEL      = 0b010,
            o_TXRATEDONE       = Open(),

            # Transmit Ports - TX Gearbox Ports
            i_TXCHARISK        = 0b00000000,
            o_TXGEARBOXREADY   = Open(),
            i_TXHEADER         = tx_header,
            i_TXSEQUENCE       = tx_sequence,
            i_TXSTARTSEQ       = 0,

            # Transmit Ports - TX Initialization and Reset Ports
            i_TXPCSRESET       = 0,
            i_TXPMARESET       = tx_pma_auto,
            o_TXRESETDONE      = tx_reset_done,

            # Transmit Ports - TX OOB signaling Ports
            o_TXCOMFINISH      = Open(),
            i_TXCOMINIT        = 0,
            i_TXCOMSAS         = 0,
            i_TXCOMWAKE        = 0,
            i_TXPDELECIDLEMODE = 0,

            # Transmit Ports - TX Polarity Control Ports
            i_TXPOLARITY       = tx_polarity,

            # Transmit Ports - TX Receiver Detection Ports
            i_TXDETECTRX       = 0,

            # Transmit Ports - TX8b/10b Encoder Ports
            i_TX8B10BBYPASS    = 0b00000000,

            # Transmit Ports - pattern Generator Ports
            i_TXPRBSSEL        = 0b000,

            # Tx Configurable Driver  Ports
            o_TXQPISENN        = Open(),
            o_TXQPISENP        = Open(),
        )
        self.specials += Instance("GTXE2_CHANNEL", **gtx_params)
        # Clocking. TX/RXOUTCLK (RXOUTCLKPMA) run at the internal 4-byte rate, linerate/32;
        # xxUSRCLK takes that and xxUSRCLK2 the half-rate 64-bit fabric clock, both from one
        # MMCM per direction so they stay phase aligned.
        txoutclk_rebuffer = Signal()
        rxoutclk_rebuffer = Signal()
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=txoutclk_rebuffer),
            Instance("BUFG", i_I=self.rxoutclk, o_O=rxoutclk_rebuffer),
        ]
        self.specials += [
        ]

        self.tx_mmcm = tx_mmcm = S7MMCM()
        tx_mmcm.register_clkin(txoutclk_rebuffer, self.tx_clk_freq*2)
        tx_mmcm.create_clkout(self.cd_eth_tx_usr, self.tx_clk_freq*2, with_reset=False)
        tx_mmcm.create_clkout(self.cd_eth_tx_raw, self.tx_clk_freq, buf=None, with_reset=False)
        self.specials += [
            Instance("BUFG", i_I=self.cd_eth_tx_raw.clk, o_O=self.cd_eth_tx.clk),
            AsyncResetSynchronizer(self.cd_eth_tx, ~tx_mmcm.locked),
        ]
        self.comb += tx_mmcm.reset.eq(tx_mmcm_reset)
        self.comb += tx_mmcm_locked.eq(tx_mmcm.locked)
        self.comb += self.tx_mmcm_locked.eq(tx_mmcm.locked)

        self.rx_mmcm = rx_mmcm = S7MMCM()
        rx_mmcm.register_clkin(rxoutclk_rebuffer, self.rx_clk_freq*2)
        rx_mmcm.create_clkout(self.cd_eth_rx_usr, self.rx_clk_freq*2, with_reset=False)
        rx_mmcm.create_clkout(self.cd_eth_rx_raw, self.rx_clk_freq, buf=None, with_reset=False)
        self.specials += [
            Instance("BUFG", i_I=self.cd_eth_rx_raw.clk, o_O=self.cd_eth_rx.clk),
            AsyncResetSynchronizer(self.cd_eth_rx, ~rx_mmcm.locked),
        ]
        self.comb += rx_mmcm.reset.eq(rx_mmcm_reset)
        self.comb += rx_mmcm_locked.eq(rx_mmcm.locked)
        self.comb += self.rx_mmcm_locked.eq(rx_mmcm.locked)

        # Transceiver init.
        self.tx_init = tx_init = ResetInserter()(GTXTXInit(sys_clk_freq, buffer_enable=True))
        self.comb += [
            tx_init.reset.eq(self.reset),
            qpll.reset.eq(tx_init.pllreset),
            tx_init.plllock.eq(qpll.lock),
            tx_reset.eq(tx_init.gtXxreset),
            tx_init.Xxresetdone.eq(tx_reset_done),
        ]
        # Re-initialise the TX PMA's clock dividers once the transmitter is otherwise up. On about
        # half of all resets they come out of GTTXRESET in the wrong state and TXOUTCLK runs at
        # linerate/24 instead of linerate/32 -- 429.687MHz rather than 322.266MHz -- with the MGT
        # reference and the QPLL both measured good. The resulting line rate is a third too fast,
        # which no partner can follow and which our own near-end loopback cannot decode either,
        # while block_lock (which describes only the receive direction) reports nothing wrong.
        # Measured from the host, a TXPMARESET pulse alone recovered every such state and left
        # every good one intact; GTTXRESET was never needed. So issue one here, after the init FSM
        # has reached READY and its 1ms watchdog is disarmed, with the MMCM held through it so it
        # relocks on the corrected TXOUTCLK rather than tracking the jump.
        #
        # This is not a consequence of the KC705's late clock bring-up. The bad states reproduced on
        # PHY resets minutes after boot, with the MGT reference measured good by a counter
        # independent of the transceiver and the QPLL locked, so it belongs to the transceiver's
        # reset sequence. That is also why it lives here rather than in software: it recurs on every
        # transceiver reset, not only at power-up, and CPU-less designs have no BIOS to issue it. The
        # board-specific part -- waiting for a reference that software has to bring up -- is left to
        # the board, which can hold the PHY in reset until the reference is valid; a BIOS could
        # reasonably do that after programming the clock. Measured on one KC705 only, at
        # 10.3125Gbps off the QPLL.
        TX_PMA_DELAY = int(1e-3*sys_clk_freq)
        TX_PMA_WIDTH = int(10e-3*sys_clk_freq)
        tx_pma_timer = Signal(max=TX_PMA_DELAY + TX_PMA_WIDTH + 1)
        self.sync += [
            If(~tx_init.done,
                tx_pma_timer.eq(0),
                tx_pma_auto.eq(0),
                self.tx_pma_done.eq(0),
            ).Elif(~self.tx_pma_done,
                tx_pma_timer.eq(tx_pma_timer + 1),
                tx_pma_auto.eq((tx_pma_timer >= TX_PMA_DELAY) &
                               (tx_pma_timer <  TX_PMA_DELAY + TX_PMA_WIDTH)),
                If(tx_pma_timer == TX_PMA_DELAY + TX_PMA_WIDTH,
                    self.tx_pma_done.eq(1),
                ),
            ),
        ]

        # Hold the transmit MMCM in reset through GTTXRESET and the TXPMARESET pulse, and for a
        # short settle after both, so it only locks on a TXOUTCLK the transceiver has finished
        # producing. It was first added as the fix for the linerate/24 fault, on the theory that the
        # MMCM was locking to a transient and landing on the wrong solution. It was not: TXOUTCLK
        # itself measured 429.687MHz at the MMCM's input, so the MMCM was faithfully scaling a
        # wrong clock, and this alone left the failure rate unchanged (3/8 bad). It stays as
        # conservative sequencing, and it is what lets the MMCM relock cleanly after tx_pma_auto.
        tx_outclk_settle = Signal(8)
        self.sync += [
            If(~qpll.lock | tx_reset | tx_pma_auto,
                tx_outclk_settle.eq(0),
                tx_mmcm_reset.eq(1),
            ).Elif(tx_outclk_settle != 0xff,
                tx_outclk_settle.eq(tx_outclk_settle + 1),
            ).Else(
                tx_mmcm_reset.eq(0),
            ),
        ]
        tx_mmcm_reset.attr.add("no_retiming")

        self.rx_init = rx_init = ResetInserter()(GTXRXInit(sys_clk_freq, buffer_enable=True))
        self.comb += [
            rx_init.reset.eq(~tx_init.done | self.reset),
            rx_init.plllock.eq(qpll.lock),
            rx_reset.eq(rx_init.gtXxreset),
            rx_init.Xxresetdone.eq(rx_reset_done),
        ]
        ps_restart = PulseSynchronizer("eth_rx", "sys")
        self.submodules += ps_restart
        self.comb += [
            ps_restart.i.eq(self.rx_reset_req),
            rx_init.restart.eq(ps_restart.o),
        ]

        # CDR lock time of 50,000 UI, as DS182 and the Xilinx wizard assume.
        cdr_lock_time    = round(sys_clk_freq*50e3/self.linerate)
        cdr_lock_counter = Signal(max=cdr_lock_time+1)
        cdr_locked       = Signal()
        self.sync += [
            If(rx_reset,
                cdr_locked.eq(0),
                cdr_lock_counter.eq(0),
            ).Elif(cdr_lock_counter != cdr_lock_time,
                cdr_lock_counter.eq(cdr_lock_counter + 1),
            ).Else(
                cdr_locked.eq(1),
            ),
            rx_mmcm_reset.eq(~cdr_locked),
        ]
        rx_mmcm_reset.attr.add("no_retiming")


# 5GBASE-R -----------------------------------------------------------------------------------------

class PMA_K7_GTX_5G_BASER(PMA_K7_GTX_10G_BASER):
    """5GBASE-R via a Kintex-7 GTX transceiver.

    Identical to the 10G chain at half the rate: the QPLL keeps its 10.3125GHz VCO and the channel
    output divider goes from d=1 to d=2, so every user clock halves.
    """
    linerate    = 5.15625e9
    tx_clk_freq = linerate/64
    rx_clk_freq = linerate/64
