#
# This file is part of LiteEth.
#
# Copyright (c) 2018 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2020-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Sergey Razumov <cyntem@gmail.com>
# Copyright (c) 2025 Patrick Urban <support@colognechip.com>
# Copyright (c) 2026 Emilien Decoux <emilien.decoux@edu.univ-fcomte.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import PulseSynchronizer

from litex.gen import *

from litex.soc.interconnect import stream

from liteeth.common import *

class PMA_SERDES_GateMate(LiteXModule):
    linerate    = 1.25e9
    def __init__(self, sys_clk_freq, refclk_freq=100e6, with_csr=True,
        tx_polarity     = 0,
        rx_polarity     = 0,
    ):
        assert refclk_freq in [100e6, 125e6]
        self.dw = dw = 32 # assert dw in [16, 32, 64]

        self.reset = Signal() # sync reset

        self.tx_clk_freq = self.linerate / dw
        self.rx_clk_freq = self.linerate / dw

        self.align    = Signal()
        self.restart  = Signal()

        self.cd_eth_tx      = ClockDomain()
        self.cd_eth_rx      = ClockDomain()

        # for specifying clock constraints. 31.25MHz clocks.
        self.txoutclk = Signal()
        self.rxoutclk = Signal()


        self.tx_data = Signal(dw)
        self.tx_char_is_k = Signal(int(dw/8))
        self.rx_data = Signal(dw)
        self.rx_char_is_k = Signal(int(dw/8))
        self.rx_table_err = Signal(int(dw/8))
        self.rx_disparity_err = Signal(int(dw/8))

        # # #

        # SerDes transceiver.
        adpll_reset   = Signal(reset=1)

        tx_reset      = Signal()
        tx_reset_done = Signal()

        rx_reset      = Signal()
        rx_reset_done = Signal()

        adpll_settings = {
            100e6: {
                'fcntrl': {64: 0x3A, 32: 0x3A, 16: 0x1A}[self.dw],
                'main_divsel': 0x1b,
                'out_divsel': {1.25e9 : 3, 3.125e9 : 1}[self.linerate],
            },
            125e6: { 
                'fcntrl': {64: 0x3A, 32: 0x3A, 16: 0x1A}[self.dw],
                'main_divsel': 0x1a,
                'out_divsel': {1.25e9 : 3, 3.125e9 : 1}[self.linerate],
            }
        }

        adpll = adpll_settings.get(refclk_freq)

        datapath_sel = {64: 3, 32: 1, 16: 0}[self.dw]

        # Work around Python's 255 argument limitation.
        self.serdes_params = serdes_params = dict(
            p_RX_RESET_TIMER_PRESC = 0,
            p_RX_RESET_OVR = 0,
            p_RX_RESET = 0,

            p_RX_PCS_RESET_TIME = 3,
            p_RX_RESET_DONE_GATE = 0,

            # RX Analog Frontend
            p_RX_PMA_RESET_TIME = 3,
            p_RX_EI_BIAS = 4,
            p_RX_EI_BW_SEL = 4,
            p_RX_EN_EI_DETECTOR_OVR = 0,
            p_RX_EN_EI_DETECTOR = 0,
            p_RX_AFE_PEAK = 15,
            p_RX_AFE_GAIN = 8,
            p_RX_AFE_VCMSEL = 4,
            p_RX_CALIB_EN = 0,
            p_RX_CALIB_OVR = 0,
            p_RX_CALIB_VAL = 0,
            p_RX_RTERM_VCMSEL = 4,
            p_RX_RTERM_PD = 0,

            # RX CDR
            p_RX_WAIT_CDR_LOCK = 0, # TODO CHECK
            p_RX_CDR_CKP = 0xF8,
            p_RX_CDR_CKI = 0,
            p_RX_CDR_TRANS_TH = 8,
            p_RX_CDR_LOCK_CFG = 0xD5,
            p_RX_CDR_FREQ_ACC = 0,
            p_RX_CDR_PHASE_ACC = 0,
            p_RX_CDR_SET_ACC_CONFIG = 0,
            p_RX_CDR_FORCE_LOCK = 0,
            p_RX_CDR_RESET_TIME = 3,
            p_RX_CDR_RESET_OVR = 0,
            p_RX_CDR_RESET = 0,

            # RX Byte and Word Alignment
            p_RX_ALIGN_MCOMMA_VALUE = 0x283,
            p_RX_MCOMMA_ALIGN_OVR = 0,
            p_RX_MCOMMA_ALIGN = 0,
            p_RX_ALIGN_PCOMMA_VALUE = 0x17C,
            p_RX_PCOMMA_ALIGN_OVR = 0,
            p_RX_PCOMMA_ALIGN = 0,
            p_RX_ALIGN_COMMA_WORD = 2, # 32 bit TODO auto sel with datapath width
            p_RX_ALIGN_COMMA_ENABLE = 0x3FF,
            p_RX_SLIDE_MODE = 0,
            p_RX_COMMA_DETECT_EN_OVR = 0,
            p_RX_COMMA_DETECT_EN = 0,
            p_RX_SLIDE = 0,

            # RX Equalizer
            p_RX_EQA_RESET_TIME = 3,
            p_RX_EQA_RESET_OVR = 0,
            p_RX_EQA_RESET = 0,
            p_RX_EQA_CKP_LF = 0xA3,
            p_RX_EQA_CKP_HF = 0xA3,
            p_RX_EQA_CKP_OFFSET = 0x01,
            p_RX_EN_EQA = 0,
            p_RX_EQA_LOCK_CFG = 0,
            p_RX_TH_MON1 = 8,
            p_RX_EN_EQA_EXT_VALUE = 0,
            p_RX_TH_MON2 = 8,
            p_RX_TAPW = 8,
            p_RX_AFE_OFFSET = 8,
            p_RX_EQA_CONFIG = 0x01C0,
            p_RX_MON_PH_OFFSET = 0,

            # RX Margin Analysis
            p_RX_EYE_MEAS_EN = 0,
            p_RX_EYE_MEAS_CFG = 0,

            # RX Elatic Buffer / Clock Correction
            p_RX_BUF_RESET_TIME = 3,
            p_RX_BUF_RESET_OVR = 0,
            p_RX_BUF_RESET = 0,
            p_RX_BUF_BYPASS = 0, # bypass buffer to use the recovered clock on RX datapath
            p_RX_CLKCOR_USE = 0,
            p_RX_CLKCOR_MIN_LAT = 32,
            p_RX_CLKCOR_MAX_LAT = 39,
            p_RX_CLKCOR_SEQ_1_0 = 0x1F7,
            p_RX_CLKCOR_SEQ_1_1 = 0x1F7,
            p_RX_CLKCOR_SEQ_1_2 = 0x1F7,
            p_RX_CLKCOR_SEQ_1_3 = 0x1F7,

            # TX+RX PRBS
            p_TX_PRBS_OVR = 0,
            p_TX_PRBS_SEL = 0,
            p_TX_PRBS_FORCE_ERR = 0,
            p_RX_PRBS_OVR = 0,
            p_RX_PRBS_SEL = 0,
            p_RX_PRBS_CNT_RESET = 0,

            # TX+RX Loopback
            p_TX_LOOPBACK_OVR = 0,
            p_TX_PMA_LOOPBACK = 0,
            p_TX_PCS_LOOPBACK = 0,
            p_RX_LOOPBACK_OVR = 0,
            p_RX_PMA_LOOPBACK = 0,
            p_RX_PCS_LOOPBACK = 0,

            # TX+RX Powerdown
            p_TX_POWER_DOWN_OVR = 0,
            p_TX_POWER_DOWN_N = 1,
            p_TX_ELEC_IDLE_OVR = 0,
            p_TX_ELEC_IDLE = 0,
            p_RX_POWER_DOWN_OVR = 0,
            p_RX_POWER_DOWN_N = 1,

            # TX+RX 8B10B
            p_TX_8B10B_EN_OVR = 0,
            p_TX_8B10B_EN = 1,
            p_RX_8B10B_EN_OVR = 0,
            p_RX_8B10B_EN = 1,
            p_RX_8B10B_BYPASS = 0,

            # TX+RX Datapath
            p_TX_DATAPATH_SEL = datapath_sel,
            p_RX_DATAPATH_SEL = datapath_sel,

            # TX+RX Polarity Control
            p_RX_POLARITY_OVR = 0,
            p_RX_POLARITY = 0,
            p_TX_POLARITY_OVR = 0,
            p_TX_POLARITY = 0,

            p_RX_PMA_RESET_OVR = 0,
            p_RX_PMA_RESET = 0,
            p_RX_PCS_RESET_OVR = 0,
            p_RX_PCS_RESET = 0,
            p_TX_SEL_PRE = 0,
            p_TX_SEL_POST = 0,
            p_TX_AMP = 30,
            p_TX_BRANCH_EN_PRE = 15,
            p_TX_BRANCH_EN_MAIN = 0x3F,
            p_TX_BRANCH_EN_POST = 15,
            p_TX_TAIL_CASCODE = 4,
            p_TX_DC_ENABLE = 63,
            p_TX_DC_OFFSET = 8,
            p_TX_CM_RAISE = 0,
            p_TX_CM_THRESHOLD_0 = 14,
            p_TX_CM_THRESHOLD_1 = 16,
            p_TX_SEL_PRE_EI = 0,
            p_TX_SEL_POST_EI = 0,
            p_TX_AMP_EI = 15,
            p_TX_BRANCH_EN_PRE_EI = 0,
            p_TX_BRANCH_EN_MAIN_EI = 0x3F,
            p_TX_BRANCH_EN_POST_EI = 0,
            p_TX_TAIL_CASCODE_EI = 4,
            p_TX_DC_ENABLE_EI = 63,
            p_TX_DC_OFFSET_EI = 0,
            p_TX_CM_RAISE_EI = 0,
            p_TX_CM_THRESHOLD_0_EI = 14,
            p_TX_CM_THRESHOLD_1_EI = 16,
            p_TX_SEL_PRE_RXDET = 0,
            p_TX_SEL_POST_RXDET = 0,
            p_TX_AMP_RXDET = 15,
            p_TX_BRANCH_EN_PRE_RXDET = 0,
            p_TX_BRANCH_EN_MAIN_RXDET = 0x3F,
            p_TX_BRANCH_EN_POST_RXDET = 0,
            p_TX_TAIL_CASCODE_RXDET = 4,
            p_TX_DC_ENABLE_RXDET = 63,
            p_TX_DC_OFFSET_RXDET = 0,
            p_TX_CM_RAISE_RXDET = 0,
            p_TX_CM_THRESHOLD_0_RXDET = 14,
            p_TX_CM_THRESHOLD_1_RXDET = 16,
            p_TX_CALIB_EN = 0,
            p_TX_CALIB_OVR = 0,
            p_TX_CALIB_VAL = 0,
            p_TX_CM_REG_KI = 0x80,
            p_TX_CM_SAR_EN = 0,
            p_TX_CM_REG_EN = 1,
            p_TX_PMA_RESET_TIME = 3,
            p_TX_PCS_RESET_TIME = 3,
            p_TX_PCS_RESET_OVR = 0,
            p_TX_PCS_RESET = 0,
            p_TX_PMA_RESET_OVR = 0,
            p_TX_PMA_RESET = 0,
            p_TX_RESET_OVR = 0,
            p_TX_RESET = 0,
            p_TX_DETECT_RX_OVR = 0,
            p_TX_DETECT_RX = 0,
            p_TX_DATA_OVR = 0,
            p_TX_DATA_CNT = 0,
            p_TX_DATA_VALID = 0,

            # ADPLL
            p_PLL_EN_ADPLL_CTRL = 1,
            p_PLL_CONFIG_SEL = 1,
            p_PLL_SET_OP_LOCK = 0,
            p_PLL_ENFORCE_LOCK = 0,
            p_PLL_DISABLE_LOCK = 0,
            p_PLL_LOCK_WINDOW = 1,
            p_PLL_FAST_LOCK = 1,
            p_PLL_SYNC_BYPASS = 0,
            p_PLL_PFD_SELECT = 0,
            p_PLL_REF_BYPASS = 0,
            p_PLL_REF_SEL = 0, # 0 for single ended, 1 for lvds
            p_PLL_REF_RTERM = 1,
            p_PLL_FCNTRL = adpll['fcntrl'],
            p_PLL_MAIN_DIVSEL = adpll['main_divsel'],
            p_PLL_OUT_DIVSEL = adpll['out_divsel'],
            p_PLL_CI = 3,
            p_PLL_CP = 80,
            p_PLL_AO = 0,
            p_PLL_SCAP = 0,
            p_PLL_FILTER_SHIFT = 2,
            p_PLL_SAR_LIMIT = 2,
            p_PLL_FT = 512,
            p_PLL_OPEN_LOOP = 0,
            p_PLL_SCAP_AUTO_CAL = 1,

            # ADPLL BISC
            p_PLL_BISC_MODE = 5,
            p_PLL_BISC_TIMER_MAX = 12,
            p_PLL_BISC_OPT_DET_IND = 0,
            p_PLL_BISC_PFD_SEL = 0,
            p_PLL_BISC_DLY_DIR = 0,
            p_PLL_BISC_COR_DLY = 1,
            p_PLL_BISC_CAL_SIGN = 1,
            p_PLL_BISC_CAL_AUTO = 1,
            p_PLL_BISC_CP_MIN = 6,
            p_PLL_BISC_CP_MAX = 30,
            p_PLL_BISC_CP_START = 6,
            p_PLL_BISC_DLY_PFD_MON_REF = 0,
            p_PLL_BISC_DLY_PFD_MON_DIV = 2,

            # Misc.q
            p_SERDES_ENABLE = 1,
            p_SERDES_AUTO_INIT = 0,
            p_SERDES_TESTMODE = 0,
        )
        serdes_params.update(
            # PLL and Misc. Ports
            i_PLL_RESET_I            = adpll_reset,
            o_PLL_CLK_O              = self.txoutclk, # 125 MHz
            i_LOOPBACK_I             = 0b00,

            # TX
            i_TX_DATA_I              = Cat(self.tx_data, self.tx_data),
            i_TX_RESET_I             = tx_reset,
            i_TX_PCS_RESET_I         = 0,
            i_TX_PMA_RESET_I         = 0,
            i_TX_POWER_DOWN_N_I      = 1,
            i_TX_POLARITY_I          = tx_polarity,
            i_TX_PRBS_SEL_I          = 0,
            i_TX_PRBS_FORCE_ERR_I    = 0,
            i_TX_8B10B_EN_I          = 1,
            i_TX_8B10B_BYPASS_I      = 0x00,
            i_TX_CHAR_IS_K_I         = Cat(self.tx_char_is_k, self.tx_char_is_k),
            i_TX_CHAR_DISPMODE_I     = 0,
            i_TX_CHAR_DISPVAL_I      = 0,
            i_TX_ELEC_IDLE_I         = 0,
            i_TX_DETECT_RX_I         = 1,
            i_TX_CLK_I               = self.txoutclk,
            o_TX_DETECT_RX_DONE_O    = Open(),
            o_TX_DETECT_RX_PRESENT_O = Open(),
            o_TX_BUF_ERR_O           = Open(),
            o_TX_RESET_DONE_O        = tx_reset_done,

            # RX
            i_RX_CLK_I               = self.txoutclk, #self.rxoutclk,
            i_RX_RESET_I             = rx_reset,
            i_RX_PMA_RESET_I         = 0,
            i_RX_EQA_RESET_I         = 0,
            i_RX_CDR_RESET_I         = 0,
            i_RX_PCS_RESET_I         = 0,
            i_RX_BUF_RESET_I         = 0,
            i_RX_POWER_DOWN_N_I      = 1,
            i_RX_POLARITY_I          = rx_polarity,
            i_RX_PRBS_SEL_I          = 0,
            i_RX_PRBS_CNT_RESET_I    = 0,
            i_RX_8B10B_EN_I          = 1,
            i_RX_8B10B_BYPASS_I      = 0x00,
            i_RX_EN_EI_DETECTOR_I    = 0,
            i_RX_COMMA_DETECT_EN_I   = self.align,
            i_RX_SLIDE_I             = 0,
            i_RX_MCOMMA_ALIGN_I      = self.align,
            i_RX_PCOMMA_ALIGN_I      = self.align,
            o_RX_DATA_O              = self.rx_data,
            o_RX_NOT_IN_TABLE_O      = self.rx_table_err,
            o_RX_CHAR_IS_COMMA_O     = Open(),
            o_RX_CHAR_IS_K_O         = self.rx_char_is_k,
            o_RX_DISP_ERR_O          = self.rx_disparity_err,
            o_RX_PRBS_ERR_O          = Open(),
            o_RX_BUF_ERR_O           = Open(),
            o_RX_BYTE_IS_ALIGNED_O   = Open(),
            o_RX_BYTE_REALIGN_O      = Open(),
            o_RX_RESET_DONE_O        = rx_reset_done,
            o_RX_EI_EN_O             = Open(),
            o_RX_CLK_O               = self.rxoutclk,
            
            # Regfile Ports
            i_REGFILE_CLK_I          = 0, #ClockSignal('sys'),
            i_REGFILE_WE_I           = 0,
            i_REGFILE_EN_I           = 0, #rf_en,
            i_REGFILE_ADDR_I         = 0, #rf_addr,
            i_REGFILE_DI_I           = 0,
            i_REGFILE_MASK_I         = 0xFFFF,
            o_REGFILE_DO_O           = Open(),#rf_do,
            o_REGFILE_RDY_O          = Open(),#rf_rdy,
        )

        self.specials += [
                Instance(
                    'CC_BUFG',
                    i_I = self.txoutclk,
                    o_O = self.cd_eth_tx.clk,
                    ),
                Instance(
                    'CC_BUFG',
                    i_I = self.rxoutclk,
                    o_O = self.cd_eth_rx.clk,
                    ),
                ]

        rx_cd_reset = Signal(reset=1)
        tx_cd_reset = Signal(reset=1)
        self.specials += AsyncResetSynchronizer(self.cd_eth_rx, rx_cd_reset)
        self.specials += AsyncResetSynchronizer(self.cd_eth_tx, tx_cd_reset)
        
        pcs_restart = PulseSynchronizer("eth_tx", "sys")
        self.submodules += pcs_restart
        self.comb += [
            pcs_restart.i.eq(self.restart),
        ]
        
        pll_reset_cycles = round(50e-3*sys_clk_freq)
        reset_counter    = Signal(max=pll_reset_cycles+1)
        
        self.reset_fsm = FSM()
        self.reset_fsm_state = Signal(4)
        self.reset_fsm.act('RESET',
            self.reset_fsm_state.eq(1),
            NextValue(adpll_reset, 1),
            NextValue(rx_cd_reset, 1),
            NextValue(tx_cd_reset, 1),
            NextValue(reset_counter, 0),
            If(~self.reset & ~pcs_restart.o,
                NextState('START'),
            )
        )
        self.reset_fsm.act('START',
            self.reset_fsm_state.eq(2),
            NextValue(reset_counter, reset_counter + 1),
            If(reset_counter >= pll_reset_cycles,
                NextValue(adpll_reset, 0),
                NextState('WAIT_SERDES_RESET'),
            ),
        )
        self.reset_fsm.act('WAIT_SERDES_RESET',
            self.reset_fsm_state.eq(3),
            If(rx_reset_done & tx_reset_done,
                NextValue(reset_counter, 0),
                NextState('WAIT_CDR_1'),
            ),
        )
        self.reset_fsm.act('WAIT_CDR_1',
            self.reset_fsm_state.eq(5),
            NextValue(reset_counter, reset_counter + 1),
            If(reset_counter >= pll_reset_cycles, # maybe a shorter wait is enough ?
                NextValue(rx_cd_reset, 0),
                NextValue(tx_cd_reset, 0),
                NextValue(reset_counter, 0),
                NextState('RUN'),
            ),
        )
        self.reset_fsm.act('RUN',
            self.reset_fsm_state.eq(6),
            If(self.reset,
                NextState('RESET'),
            ),
            If(reset_counter < pll_reset_cycles,
                NextValue(reset_counter, reset_counter + 1),
            ).Elif(pcs_restart.o,
                NextValue(rx_cd_reset, 1),
                NextValue(tx_cd_reset, 1),
                NextValue(reset_counter, 0),
                NextState('WAIT_CDR_1'),
            ),
        )
        
        if with_csr:
            self.status = CSRStatus(fields=[
                CSRField("fsm_state", size=4,  offset=0),
            ])

            self.comb += [
                self.status.fields.fsm_state.eq(self.reset_fsm_state),
            ]


    def do_finalize(self):
        self.specials += Instance("CC_SERDES", **self.serdes_params)
