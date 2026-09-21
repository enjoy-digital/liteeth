#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField

from liteeth.common import *
from liteeth.phy.pcs_1000basex import PCS

# GW5 Raw SerDes -----------------------------------------------------------------------------------

class GW5SerDes(LiteXModule):
    """Raw 1.25 Gb/s, 10-bit interface on GW5AST-138B Q1 lane 0 or 1.

    The caller supplies the PCS and synchronizes resets to the TX/RX clocks.
    The RX interface can have gaps; rx_valid qualifies rx_data. Neither this
    interface nor hardware comma alignment guarantees deterministic latency.
    """
    def __init__(self, platform, lane=0):
        if platform.devicename != "GW5AST-138B":
            raise ValueError("GW5SerDes currently supports GW5AST-138B.")
        if lane not in (0, 1):
            raise ValueError("GW5SerDes supports Q1 lanes 0 and 1.")
        self.reset    = Signal()
        # GowinSynthesis otherwise merges equivalent registers driven by the
        # independent TX and recovered RX clock outputs of the GTR primitive.
        self.tx_clk   = Signal(attr={("syn_keep", 1)})
        self.rx_clk   = Signal(attr={("syn_keep", 1)})
        self.tx_data  = Signal(10)
        self.rx_data  = Signal(88)
        self.rx_valid = Signal()
        self.rx_empty = Signal()
        self.pll_lock = Signal()
        self.cdr_lock = Signal()
        self.aligned  = Signal()

        # SerDes -----------------------------------------------------------------------------------
        # Unused fabric controls are tied low; the CSR configuration selects their internal controls.
        serdes_params = dict(
            p_POSITION                    = "Q1",
            i_FABRIC_CLK_LIFE_DIV_I       = Constant(0, 2),
            i_FABRIC_CM0_RXCLK_OE_L_I     = Constant(0, 1),
            i_FABRIC_CM0_RXCLK_OE_R_I     = Constant(0, 1),
            i_FABRIC_PMA_PD_REFHCLK_I     = Constant(0, 1),
            i_FABRIC_REFCLK1_INPUT_SEL_I  = Constant(0, 3),
            i_FABRIC_REFCLK_INPUT_SEL_I   = Constant(0, 3),
            i_FABRIC_REFCLK_OE_L_I        = Constant(0, 1),
            i_FABRIC_REFCLK_OE_R_I        = Constant(0, 1),
            i_FABRIC_REFCLK_OUTPUT_SEL_I  = Constant(0, 5),
            i_REFCLKM0_I                  = Constant(0, 1),
            i_REFCLKM1_I                  = Constant(0, 1),
            i_REFCLKP0_I                  = Constant(0, 1),
            i_REFCLKP1_I                  = Constant(0, 1),
            i_FABRIC_BURN_IN_I            = Constant(0, 1),
            i_FABRIC_CK_SOC_DIV_I         = Constant(0, 2),
            i_FABRIC_CLK_REF_CORE_I       = Constant(0, 1),
            i_FABRIC_CMU1_REFCLK_GATE_I   = Constant(0, 1),
            i_FABRIC_CMU_REFCLK_GATE_I    = Constant(0, 1),
            i_FABRIC_GLUE_MAC_INIT_INFO_I = Constant(0, 1),
            i_FABRIC_REFCLK_GATE_I        = Constant(0, 1),
            i_FABRIC_CMU0_RESETN_I        = Constant(0, 1),
            i_FABRIC_CMU0_PD_I            = Constant(0, 1),
            i_FABRIC_CMU0_IDDQ_I          = Constant(0, 1),
            i_FABRIC_CMU1_RESETN_I        = Constant(0, 1),
            i_FABRIC_CMU1_PD_I            = Constant(0, 1),
            i_FABRIC_CMU1_IDDQ_I          = Constant(0, 1),
            i_FABRIC_PLL_CDN_I            = Constant(0, 1),
            i_FABRIC_CM1_PD_REFCLK_DET_I  = Constant(0, 1),
            i_FABRIC_CM0_PD_REFCLK_DET_I  = Constant(0, 1),
            i_FABRIC_POR_N_I              = Constant(0, 1),
            i_FABRIC_QUAD_MCU_REQ_I       = Constant(0, 1),
            i_CK_AHB_I                    = Constant(0, 1),
            i_AHB_RSTN                    = Constant(0, 1),
            i_TEST_DEC_EN                 = Constant(0, 1),
            i_QUAD_PCIE_CLK               = Constant(0, 1),
            i_PCIE_DIV2_REG               = Constant(0, 1),
            i_PCIE_DIV4_REG               = Constant(0, 1),
            i_PMAC_LN_RSTN                = Constant(0, 1),
        )
        for n in range(4):
            serdes_params.update({
                f"i_LN{n}_RXM_I"                : Constant(0, 1),
                f"i_LN{n}_RXP_I"                : Constant(0, 1),
                f"i_FABRIC_LN{n}_CTRL_I"        : Constant(0, 43),
                f"i_FABRIC_LN{n}_IDDQ_I"        : Constant(0, 1),
                f"i_FABRIC_LN{n}_PD_I"          : Constant(0, 3),
                f"i_FABRIC_LN{n}_RATE_I"        : Constant(0, 2),
                f"i_FABRIC_LN{n}_RSTN_I"        : Constant(0, 1),
                f"i_FABRIC_LN{n}_TXDATA_I"      : Constant(0, 80),
                f"i_LANE{n}_PCS_RX_RST"         : Constant(0, 1),
                f"i_LANE{n}_ALIGN_TRIGGER"      : Constant(0, 1),
                f"i_LANE{n}_CHBOND_START"       : Constant(0, 1),
                f"i_LANE{n}_PCS_TX_RST"         : Constant(0, 1),
                f"i_LANE{n}_FABRIC_RX_CLK"      : Constant(0, 1),
                f"i_LANE{n}_FABRIC_C2I_CLK"     : Constant(0, 1),
                f"i_LANE{n}_FABRIC_TX_CLK"      : Constant(0, 1),
                f"i_LANE{n}_RX_IF_FIFO_RDEN"    : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_RESETN_I" : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_PD_I"     : Constant(0, 1),
                f"i_FABRIC_LN{n}_CPLL_IDDQ_I"   : Constant(0, 1),
                f"i_FABRIC_LN{n}_CTRL_I_H"      : Constant(0, 43),
                f"i_FABRIC_LN{n}_PD_I_H"        : Constant(0, 3),
                f"i_FABRIC_LN{n}_RATE_I_H"      : Constant(0, 2),
                f"i_FABRIC_LN{n}_TX_VLD_IN"     : Constant(0, 1),
            })
        serdes_params.update({
            f"i_FABRIC_LN{lane}_RSTN_I"         : ~self.reset,
            f"i_LANE{lane}_PCS_TX_RST"          : self.reset,
            f"i_LANE{lane}_PCS_RX_RST"          : self.reset,
            f"i_LANE{lane}_FABRIC_TX_CLK"       : self.tx_clk,
            f"i_LANE{lane}_FABRIC_RX_CLK"       : self.rx_clk,
            f"i_FABRIC_LN{lane}_TXDATA_I"       : Cat(self.tx_data, Constant(0, 70)),
            f"i_FABRIC_LN{lane}_TX_VLD_IN"      : 1,
            f"i_LANE{lane}_RX_IF_FIFO_RDEN"     : ~self.rx_empty,
            f"o_LANE{lane}_PCS_TX_O_FABRIC_CLK" : self.tx_clk,
            f"o_LANE{lane}_PCS_RX_O_FABRIC_CLK" : self.rx_clk,
            f"o_FABRIC_LN{lane}_RXDATA_O"       : self.rx_data,
            f"o_FABRIC_LN{lane}_RX_VLD_OUT"     : self.rx_valid,
            f"o_LANE{lane}_RX_IF_FIFO_EMPTY"    : self.rx_empty,
            f"o_FABRIC_LANE{lane}_CMU_OK_O"     : self.pll_lock,
            f"o_FABRIC_LN{lane}_PMA_RX_LOCK_O"  : self.cdr_lock,
            f"o_LANE{lane}_ALIGN_LINK"          : self.aligned,
        })
        self.specials += Instance("GTR12_QUAD", **serdes_params)

        # Configuration ----------------------------------------------------------------------------
        # Write the embedded configuration in the gateware directory when Gowin runs.
        platform.toolchain.additional_tcl_commands += [
            'set serdes_csr [open "gw5_1000basex.csr" w]',
            'puts -nonewline $serdes_csr {' + _serdes_csr[lane] + '}',
            'close $serdes_csr',
            'set_csr gw5_1000basex.csr',
        ]


# GW5 1000BASE-X PHY --------------------------------------------------------------------------------

class GW5_1000BASEX(LiteXModule):
    """GW5AST-138B 1000BASE-X PHY on Q1 lane 0 or 1, using a 100 MHz Q1 REFCLK1.

    The hard SerDes uses a raw 10-bit interface at 125 MHz. LiteEth implements the
    8b/10b PCS and autonegotiation; the SerDes performs comma alignment. The embedded
    CSR configuration initializes the analog/clocking blocks during FPGA configuration.
    Dedicated serial and reference-clock pins are selected by the SerDes configuration,
    as in Gowin's generated GTR12_QUAD wrapper.
    """
    dw          = 8
    linerate    = 1.25e9
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6

    def __init__(self, platform, with_csr=True, pcs_kwargs=None, lane=0):
        if platform.devicename != "GW5AST-138B":
            raise ValueError("GW5_1000BASEX currently supports GW5AST-138B.")
        if lane not in (0, 1):
            raise ValueError("GW5_1000BASEX supports Q1 lanes 0 and 1.")

        self.reset     = Signal()
        self.pll_lock  = Signal()
        self.cdr_lock  = Signal()
        self.aligned   = Signal()
        self.cd_eth_tx = ClockDomain()
        self.cd_eth_rx = ClockDomain()

        # PCS --------------------------------------------------------------------------------------
        pcs_kwargs = {} if pcs_kwargs is None else dict(pcs_kwargs)
        pcs_kwargs.setdefault("eth_tx_clk_freq", self.tx_clk_freq)
        self.pcs = pcs = PCS(lsb_first=True, **pcs_kwargs)
        self.link_up = pcs.link_up
        if with_csr:
            self.add_csr()

        # # #

        # MAC Interface ----------------------------------------------------------------------------
        # Register the MAC boundary to keep the 125 MHz PCS paths local.
        self.tx_buffer = tx_buffer = ClockDomainsRenamer("eth_tx")(
            stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
        self.rx_buffer = rx_buffer = ClockDomainsRenamer("eth_rx")(
            stream.Buffer(eth_phy_description(self.dw), pipe_ready=True))
        self.sink   = tx_buffer.sink
        self.source = rx_buffer.source
        self.comb += [
            tx_buffer.source.connect(pcs.sink),
            pcs.source.connect(rx_buffer.sink),
        ]

        # SerDes Datapath --------------------------------------------------------------------------
        tx_data  = Signal(10)
        rx_data  = Signal(88)
        rx_valid = Signal()
        self.sync.eth_tx += tx_data.eq(pcs.tbi_tx)
        self.sync.eth_rx += [
            pcs.tbi_rx.eq(rx_data[:10]),
            pcs.tbi_rx_ce.eq(rx_valid),
        ]

        # Clocking / Reset -------------------------------------------------------------------------
        reset = self.reset | ResetSignal("sys")
        self.specials += [
            AsyncResetSynchronizer(self.cd_eth_tx, reset | ~self.pll_lock),
            AsyncResetSynchronizer(self.cd_eth_rx, reset | ~self.cdr_lock),
        ]

        # Raw SerDes -------------------------------------------------------------------------------
        self.serdes = serdes = GW5SerDes(platform, lane=lane)
        self.comb += [
            serdes.reset.eq(reset),
            serdes.tx_data.eq(tx_data),
            rx_data.eq(serdes.rx_data),
            rx_valid.eq(serdes.rx_valid),
            self.cd_eth_tx.clk.eq(serdes.tx_clk),
            self.cd_eth_rx.clk.eq(serdes.rx_clk),
            self.pll_lock.eq(serdes.pll_lock),
            self.cdr_lock.eq(serdes.cdr_lock),
            self.aligned.eq(serdes.aligned),
        ]

    def add_csr(self):
        self._reset = CSRStorage(fields=[
            CSRField("reset", description="PHY reset."),
        ])
        self._status = CSRStatus(fields=[
            CSRField("pll_lock", description="Transmit PLL locked."),
            CSRField("cdr_lock", description="Receive clock recovery locked."),
            CSRField("aligned",  description="Receive comma alignment established."),
            CSRField("link_up",  description="PCS autonegotiation completed."),
        ])
        self.comb += self.reset.eq(self._reset.fields.reset)
        self.specials += MultiReg(Cat(self.pll_lock, self.cdr_lock, self.aligned, self.link_up),
            self._status.status)

# SerDes Configuration -----------------------------------------------------------------------------
# Generated with Gowin 1.9.12. Keep the TOML source below with the register writes so configuration
# changes can be regenerated; see doc/gw5_1000basex.md. Normal builds only need the register writes.

_serdes_csr = {
    0 : """\
# GW5AST-138B Q1 lane 0: 100 MHz REFCLK1, 1.25 Gb/s, raw 10-bit PCS.
upar_write_driver(0xb00000,0x00FFAA55) # top.checkRegAcess
upar_write_driver(0x908104,0x00022322) # q1.qpll0.reset
upar_write_driver(0x908000,0x00022322) # q1.qpll1.reset
upar_write_driver(0x90a004,0x00022322) # q1.ln0.cpll.reset
upar_write_driver(0x808760,0x0001D010) # q0.cfg_refclk_mux
upar_write_driver(0x808764,0x00005010) # q0.cfg_refclk_mux
upar_write_driver(0x908760,0x00015410) # q1.cfg_refclk_mux
upar_write_driver(0x908764,0x00005010) # q1.cfg_refclk_mux
upar_write_driver(0xc10008,0x00000003) # q1.cfg_quad_cmn
upar_write_driver(0x900b91,0x0000F100) # q1.cfg_quad_cmn
upar_write_driver(0x900bfa,0x00020000) # q1.cfg_quad_cmn
upar_write_driver(0x908298,0x00000001) # q1.cfg_quad_cmn
upar_write_driver(0x90829c,0x0000001E) # q1.cfg_quad_cmn
upar_write_driver(0x908900,0x00000001) # q1.cfg_quad_mcu
upar_write_driver(0x908904,0x000050FF) # q1.cfg_quad_mcu
upar_write_driver(0x908908,0x00005F00) # q1.cfg_quad_mcu
upar_write_driver(0x90890c,0x00006080) # q1.cfg_quad_mcu
upar_write_driver(0x908910,0x00005800) # q1.cfg_quad_mcu
upar_write_driver(0x908914,0x00006001) # q1.cfg_quad_mcu
upar_write_driver(0x908918,0x000043FF) # q1.cfg_quad_mcu
upar_write_driver(0x90891c,0x0000A000) # q1.cfg_quad_mcu
upar_write_driver(0x9003a9,0x00008000) # q1.ln0.cfg_ln
upar_write_driver(0x909070,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x909074,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x90906c,0x00020110) # q1.ln0.cfg_ln
upar_write_driver(0x900200,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x90926c,0x00020110) # q1.ln1.cfg_ln
upar_write_driver(0x900400,0x00000055) # q1.ln1.cfg_ln
upar_write_driver(0x900402,0x00550000) # q1.ln1.cfg_ln
upar_write_driver(0x90946c,0x00020110) # q1.ln2.cfg_ln
upar_write_driver(0x900600,0x00000055) # q1.ln2.cfg_ln
upar_write_driver(0x900602,0x00550000) # q1.ln2.cfg_ln
upar_write_driver(0x90966c,0x00020110) # q1.ln3.cfg_ln
upar_write_driver(0x900800,0x00000055) # q1.ln3.cfg_ln
upar_write_driver(0x900802,0x00550000) # q1.ln3.cfg_ln
upar_write_driver(0x901940,0x00000000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901941,0x00004000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901942,0x00010000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901943,0x58000000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901944,0x00000001) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901945,0x00006000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901946,0x00030000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901947,0x41000000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901948,0x00000000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x901949,0x0000A000) # q1.ln0.cfg_txdrv_mcu
upar_write_driver(0x908200,0x0000000B) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90821c,0x007F00BF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908220,0x01FF01FF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908224,0x00030003) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90827c,0x00000000) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908288,0x89ABCDEF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90828c,0x01234567) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908710,0x00000008) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908714,0x04000700) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908280,0x00000014) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908284,0x00000014) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x90823c,0x191A1A1A) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908240,0x18181919) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908244,0x17171718) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908248,0x15161616) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x90824c,0x14151515) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908250,0x13141414) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908254,0x12131313) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908258,0x12121212) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x90825c,0x11111111) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908260,0x10101011) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908264,0x10101010) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908268,0x0F0F0F0F) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x90826c,0x0E0F0F0F) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908270,0x0E0E0E0E) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908274,0x0D0E0E0E) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908278,0x0D0D0D0D) # q1.ln0.cfg_txdrv_cal
upar_write_driver(0x908300,0x0000000B) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90831c,0x007F00BF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908320,0x01FF01FF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908324,0x00030003) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90837c,0x00000000) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908388,0x89ABCDEF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90838c,0x01234567) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908720,0x00000008) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908724,0x04000700) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908400,0x0000000B) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90841c,0x007F00BF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908420,0x01FF01FF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908424,0x00030003) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90847c,0x00000000) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908488,0x89ABCDEF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90848c,0x01234567) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908730,0x00000008) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908734,0x04000700) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908500,0x0000000B) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90851c,0x007F00BF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908520,0x01FF01FF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908524,0x00030003) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90857c,0x00000000) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908588,0x89ABCDEF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90858c,0x01234567) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908740,0x00000008) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908744,0x04000700) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x808984,0x00000000) # q0.cfg_cmx_refclk_det_sel
upar_write_driver(0x908808,0x00000003) # q1.preload_init_pll
upar_write_driver(0x90880c,0x00000011) # q1.preload_init_pll
upar_write_driver(0x90886c,0x00000001) # q1.preload_init_pll
upar_write_driver(0x90a044,0x00000001) # q1.ln0.cpll.cfg_cpll_cmn
upar_write_driver(0x90a050,0x00000064) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a054,0x00000064) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a058,0x00000064) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a02c,0x000900B0) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a030,0x00000005) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a034,0x00000404) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x90a038,0x006400C8) # q1.ln0.cpll.cfg_gcfsm
upar_write_driver(0x9003a5,0x00000300) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a008,0x00000001) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a000,0x00000001) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a03c,0x00000003) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a00c,0x10320108) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a014,0x00000F01) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a018,0x01B50008) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a01c,0x000041C3) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a024,0x06674080) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a020,0x00000015) # q1.ln0.cpll.cfg_pma_force
upar_write_driver(0x90a100,0x60015800) # q1.ln0.cpll.cfg_cpll_mcu
upar_write_driver(0x90a104,0x0000A000) # q1.ln0.cpll.cfg_cpll_mcu
upar_write_driver(0x9082a0,0x00007510) # q1.ln0.cpll.cfg_txlane_clktree
upar_write_driver(0x90a05c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a25c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a45c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a65c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90815c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90806c,0x00000000) # q1.preload_init_pll
upar_write_driver(0xc10008,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x900333,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900533,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900733,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900933,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x90882c,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908840,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908854,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908868,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x900e4f,0x01000000) # q1.preload_spec_phy
upar_write_driver(0x900331,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900531,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900731,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900931,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900a1a,0x00010000) # q1.preload_spec_phy
upar_write_driver(0x90881c,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908820,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908824,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908828,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908830,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908834,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908838,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x90883c,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908844,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908848,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x90884c,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908850,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908858,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x90885c,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908860,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908864,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900258,0x00000080) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90037b,0xFA000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90037c,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900379,0x0000FA00) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90037a,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900385,0x0000FA00) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900386,0x00FA0000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900232,0x00FA0000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900233,0xFA000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x9082b8,0x00000020) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90025b,0x79000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90025c,0x000000D2) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900254,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900260,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900261,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900253,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90025e,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90025f,0x00000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x9082bc,0x00000111) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900ad3,0x3F000000) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900ad5,0x00000700) # q1.ln0.CDR.config_cdr
upar_write_driver(0x900ad4,0x00000007) # q1.ln0.CDR.config_cdr
upar_write_driver(0x90033e,0x00010000) # q1.ln0.CDR.toggle_rxsd
upar_write_driver(0x90033e,0x00000000) # q1.ln0.CDR.toggle_rxsd
upar_write_driver(0x9082c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9083c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9084c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9085c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x908200,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908300,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908400,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908500,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908238,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908338,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908438,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908538,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908234,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908334,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908434,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908534,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908884,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909000,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909200,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909400,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909600,0x00000000) # q1.preload_fpcs
upar_write_driver(0x908870,0x00000000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900202,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900402,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900602,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900802,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x90906c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90926c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90946c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90966c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x908918,0x00004000) # q1.preload_mcu
upar_write_driver(0x90891c,0x00006300) # q1.preload_mcu
upar_write_driver(0x908920,0x000051FF) # q1.preload_mcu
upar_write_driver(0x908924,0x000052FF) # q1.preload_mcu
upar_write_driver(0x908928,0x00005F00) # q1.preload_mcu
upar_write_driver(0x90892c,0x00006080) # q1.preload_mcu
upar_write_driver(0x908930,0x000043A2) # q1.preload_mcu
upar_write_driver(0x908934,0x0000A000) # q1.preload_mcu
upar_write_driver(0xc10008,0x00000003) # q1.pll_release_reset
upar_write_driver(0x90a004,0x00022233) # q1.ln0.cpll.release_reset
upar_write_driver(0x900256,0x00000000) # q1.ln0.setNearEndSerialLoopBack
upar_write_driver(0x908888,0x00000000) # q1.ln0.setParallelTx2RxLoopBack
upar_write_driver(0x90906c,0x00020110) # q1.ln0.setParallelTx2RxLoopBack
upar_write_driver(0x900201,0x00000000) # q1.ln0.setParallelTx2RxLoopBack
upar_write_driver(0x90032d,0x00000000) # q1.ln0.setParallelTx2RxLoopBack
upar_write_driver(0x9082dc,0x00000031) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x9003a7,0x00000000) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x900224,0x00000041) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x900225,0x00004100) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x900226,0x00410000) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x900227,0x41000000) # q1.ln0.cfg_rx_couple_mode
upar_write_driver(0x909068,0x000001FB) # q1.ln0.cfg_fpcs
upar_write_driver(0x90906c,0x00020110) # q1.ln0.cfg_fpcs
upar_write_driver(0x908620,0x00000006) # q1.ln0.cfg_tx_GearFIFO
upar_write_driver(0x908624,0x0008020C) # q1.ln0.cfg_tx_GearFIFO
upar_write_driver(0x908600,0x0000001A) # q1.ln0.cfg_rx_GearFIFO
upar_write_driver(0x908604,0x0008020C) # q1.ln0.cfg_rx_GearFIFO
upar_write_driver(0x909010,0x0000017C) # q1.ln0.cfg_word_align_comma
upar_write_driver(0x909014,0x000003FF) # q1.ln0.cfg_word_align_comma
upar_write_driver(0x909020,0x0008007C) # q1.ln0.cfg_bonding
upar_write_driver(0x909088,0x00000000) # q1.ln0.cfg_bonding
upar_write_driver(0x90907c,0x0000007C) # q1.ln0.cfg_bonding
upar_write_driver(0x909080,0x0000007C) # q1.ln0.cfg_bonding
upar_write_driver(0x909084,0x0000007C) # q1.ln0.cfg_bonding
upar_write_driver(0x908888,0x00000100) # q1.ln0.cfg_bonding
upar_write_driver(0x90902c,0x0010007C) # q1.ln0.cfg_ctc
upar_write_driver(0x909078,0x0000007C) # q1.ln0.cfg_ctc
upar_write_driver(0x90903c,0x00000008) # q1.ln0.cfg_tx_data_manipulation
upar_write_driver(0x909008,0x00000008) # q1.ln0.cfg_rx_data_manipulation
upar_write_driver(0x90882c,0x00707120) # q1.release_ln
upar_write_driver(0x908840,0x00707120) # q1.release_ln
upar_write_driver(0x908854,0x00707120) # q1.release_ln
upar_write_driver(0x908868,0x00707120) # q1.release_ln
""",
    1 : """\
# GW5AST-138B Q1 lane 1: 100 MHz REFCLK1, 1.25 Gb/s, raw 10-bit PCS.
upar_write_driver(0xb00000,0x00FFAA55) # top.checkRegAcess
upar_write_driver(0x908104,0x00022322) # q1.qpll0.reset
upar_write_driver(0x908000,0x00022322) # q1.qpll1.reset
upar_write_driver(0x90a204,0x00022322) # q1.ln1.cpll.reset
upar_write_driver(0x808760,0x0001D010) # q0.cfg_refclk_mux
upar_write_driver(0x808764,0x00005010) # q0.cfg_refclk_mux
upar_write_driver(0x908760,0x00015410) # q1.cfg_refclk_mux
upar_write_driver(0x908764,0x00005010) # q1.cfg_refclk_mux
upar_write_driver(0xc10008,0x00000003) # q1.cfg_quad_cmn
upar_write_driver(0x900b91,0x0000F300) # q1.cfg_quad_cmn
upar_write_driver(0x900bfa,0x00020000) # q1.cfg_quad_cmn
upar_write_driver(0x908398,0x00000001) # q1.cfg_quad_cmn
upar_write_driver(0x90839c,0x0000001E) # q1.cfg_quad_cmn
upar_write_driver(0x908900,0x00000001) # q1.cfg_quad_mcu
upar_write_driver(0x908904,0x000050FF) # q1.cfg_quad_mcu
upar_write_driver(0x908908,0x00005F00) # q1.cfg_quad_mcu
upar_write_driver(0x90890c,0x00006080) # q1.cfg_quad_mcu
upar_write_driver(0x908910,0x00005800) # q1.cfg_quad_mcu
upar_write_driver(0x908914,0x00006001) # q1.cfg_quad_mcu
upar_write_driver(0x908918,0x000044FF) # q1.cfg_quad_mcu
upar_write_driver(0x90891c,0x0000A000) # q1.cfg_quad_mcu
upar_write_driver(0x9003a9,0x00008000) # q1.ln0.cfg_ln
upar_write_driver(0x909070,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x909074,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x90906c,0x00020110) # q1.ln0.cfg_ln
upar_write_driver(0x900200,0x00000000) # q1.ln0.cfg_ln
upar_write_driver(0x9005a9,0x00008000) # q1.ln1.cfg_ln
upar_write_driver(0x909270,0x00000000) # q1.ln1.cfg_ln
upar_write_driver(0x909274,0x00000000) # q1.ln1.cfg_ln
upar_write_driver(0x90926c,0x00020110) # q1.ln1.cfg_ln
upar_write_driver(0x900400,0x00000000) # q1.ln1.cfg_ln
upar_write_driver(0x90946c,0x00020110) # q1.ln2.cfg_ln
upar_write_driver(0x900600,0x00000055) # q1.ln2.cfg_ln
upar_write_driver(0x900602,0x00550000) # q1.ln2.cfg_ln
upar_write_driver(0x90966c,0x00020110) # q1.ln3.cfg_ln
upar_write_driver(0x900800,0x00000055) # q1.ln3.cfg_ln
upar_write_driver(0x900802,0x00550000) # q1.ln3.cfg_ln
upar_write_driver(0x908200,0x0000000B) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90821c,0x007F00BF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908220,0x01FF01FF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908224,0x00030003) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90827c,0x00000000) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908288,0x89ABCDEF) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x90828c,0x01234567) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908710,0x00000008) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x908714,0x04000700) # q1.ln0.cfg_txdrv_cmn
upar_write_driver(0x901970,0x00000000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901971,0x00004000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901972,0x00010000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901973,0x58000000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901974,0x00000001) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901975,0x00006000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901976,0x00030000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901977,0x41000000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901978,0x00000000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x901979,0x0000A000) # q1.ln1.cfg_txdrv_mcu
upar_write_driver(0x908300,0x0000000B) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90831c,0x007F00BF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908320,0x01FF01FF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908324,0x00030003) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90837c,0x00000000) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908388,0x89ABCDEF) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x90838c,0x01234567) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908720,0x00000008) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908724,0x04000700) # q1.ln1.cfg_txdrv_cmn
upar_write_driver(0x908380,0x00000014) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908384,0x00000014) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x90833c,0x191A1A1A) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908340,0x18181919) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908344,0x17171718) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908348,0x15161616) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x90834c,0x14151515) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908350,0x13141414) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908354,0x12131313) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908358,0x12121212) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x90835c,0x11111111) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908360,0x10101011) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908364,0x10101010) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908368,0x0F0F0F0F) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x90836c,0x0E0F0F0F) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908370,0x0E0E0E0E) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908374,0x0D0E0E0E) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908378,0x0D0D0D0D) # q1.ln1.cfg_txdrv_cal
upar_write_driver(0x908400,0x0000000B) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90841c,0x007F00BF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908420,0x01FF01FF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908424,0x00030003) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90847c,0x00000000) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908488,0x89ABCDEF) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x90848c,0x01234567) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908730,0x00000008) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908734,0x04000700) # q1.ln2.cfg_txdrv_cmn
upar_write_driver(0x908500,0x0000000B) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90851c,0x007F00BF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908520,0x01FF01FF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908524,0x00030003) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90857c,0x00000000) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908588,0x89ABCDEF) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x90858c,0x01234567) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908740,0x00000008) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x908744,0x04000700) # q1.ln3.cfg_txdrv_cmn
upar_write_driver(0x808984,0x00000000) # q0.cfg_cmx_refclk_det_sel
upar_write_driver(0x908808,0x00000003) # q1.preload_init_pll
upar_write_driver(0x90880c,0x00000011) # q1.preload_init_pll
upar_write_driver(0x90886c,0x00000001) # q1.preload_init_pll
upar_write_driver(0x90a244,0x00000001) # q1.ln1.cpll.cfg_cpll_cmn
upar_write_driver(0x90a250,0x00000064) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a254,0x00000064) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a258,0x00000064) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a22c,0x000900B0) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a230,0x00000005) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a234,0x00000404) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x90a238,0x006400C8) # q1.ln1.cpll.cfg_gcfsm
upar_write_driver(0x9005a5,0x00000300) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a208,0x00000001) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a200,0x00000001) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a23c,0x00000003) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a20c,0x10320108) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a214,0x00000F01) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a218,0x01B50008) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a21c,0x000041C3) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a224,0x06674080) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a220,0x00000015) # q1.ln1.cpll.cfg_pma_force
upar_write_driver(0x90a300,0x60015800) # q1.ln1.cpll.cfg_cpll_mcu
upar_write_driver(0x90a304,0x0000A000) # q1.ln1.cpll.cfg_cpll_mcu
upar_write_driver(0x9083a0,0x00007510) # q1.ln1.cpll.cfg_txlane_clktree
upar_write_driver(0x90a05c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a25c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a45c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90a65c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90815c,0x00000000) # q1.preload_init_pll
upar_write_driver(0x90806c,0x00000000) # q1.preload_init_pll
upar_write_driver(0xc10008,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x900333,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900533,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900733,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x900933,0x48000000) # q1.preload_spec_phy
upar_write_driver(0x90882c,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908840,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908854,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x908868,0x00707320) # q1.preload_spec_phy
upar_write_driver(0x900e4f,0x01000000) # q1.preload_spec_phy
upar_write_driver(0x900331,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900531,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900731,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900931,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900a1a,0x00010000) # q1.preload_spec_phy
upar_write_driver(0x90881c,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908820,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908824,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908828,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908830,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908834,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908838,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x90883c,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908844,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x908848,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x90884c,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908850,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x908858,0xFFFFFFFF) # q1.preload_spec_phy
upar_write_driver(0x90885c,0x000007FF) # q1.preload_spec_phy
upar_write_driver(0x908860,0x00000001) # q1.preload_spec_phy
upar_write_driver(0x908864,0x00000000) # q1.preload_spec_phy
upar_write_driver(0x900458,0x00000080) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90057b,0xFA000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90057c,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900579,0x0000FA00) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90057a,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900585,0x0000FA00) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900586,0x00FA0000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900432,0x00FA0000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900433,0xFA000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x9083b8,0x00000020) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90045b,0x79000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90045c,0x000000D2) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900454,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900460,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900461,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900453,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90045e,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90045f,0x00000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x9083bc,0x00000111) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900ad3,0x3F000000) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900ad5,0x00000700) # q1.ln1.CDR.config_cdr
upar_write_driver(0x900ad4,0x00000007) # q1.ln1.CDR.config_cdr
upar_write_driver(0x90053e,0x00010000) # q1.ln1.CDR.toggle_rxsd
upar_write_driver(0x90053e,0x00000000) # q1.ln1.CDR.toggle_rxsd
upar_write_driver(0x9082c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9083c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9084c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x9085c0,0x00000010) # q1.preload_spec_phy
upar_write_driver(0x908200,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908300,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908400,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908500,0x00000002) # q1.preload_spec_phy
upar_write_driver(0x908238,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908338,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908438,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908538,0x00000B00) # q1.preload_spec_phy
upar_write_driver(0x908234,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908334,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908434,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908534,0x0000E000) # q1.preload_spec_phy
upar_write_driver(0x908884,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909000,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909200,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909400,0x00000000) # q1.preload_fpcs
upar_write_driver(0x909600,0x00000000) # q1.preload_fpcs
upar_write_driver(0x908870,0x00010000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900202,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900402,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900602,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x900802,0x00110000) # q1.preload_fpcs_clock_root
upar_write_driver(0x90906c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90926c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90946c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x90966c,0x00000110) # q1.preload_fpcs_clock_root
upar_write_driver(0x908918,0x00004000) # q1.preload_mcu
upar_write_driver(0x90891c,0x00006300) # q1.preload_mcu
upar_write_driver(0x908920,0x000051FF) # q1.preload_mcu
upar_write_driver(0x908924,0x000052FF) # q1.preload_mcu
upar_write_driver(0x908928,0x00005F00) # q1.preload_mcu
upar_write_driver(0x90892c,0x00006080) # q1.preload_mcu
upar_write_driver(0x908930,0x000044A2) # q1.preload_mcu
upar_write_driver(0x908934,0x0000A000) # q1.preload_mcu
upar_write_driver(0xc10008,0x00000003) # q1.pll_release_reset
upar_write_driver(0x90a204,0x00022233) # q1.ln1.cpll.release_reset
upar_write_driver(0x900456,0x00000000) # q1.ln1.setNearEndSerialLoopBack
upar_write_driver(0x90888c,0x00000000) # q1.ln1.setParallelTx2RxLoopBack
upar_write_driver(0x90926c,0x00020110) # q1.ln1.setParallelTx2RxLoopBack
upar_write_driver(0x900401,0x00000000) # q1.ln1.setParallelTx2RxLoopBack
upar_write_driver(0x90052d,0x00000000) # q1.ln1.setParallelTx2RxLoopBack
upar_write_driver(0x9084dc,0x00000031) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x9005a7,0x00000000) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x900424,0x00000041) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x900425,0x00004100) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x900426,0x00410000) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x900427,0x41000000) # q1.ln1.cfg_rx_couple_mode
upar_write_driver(0x909268,0x000001FB) # q1.ln1.cfg_fpcs
upar_write_driver(0x90926c,0x00020110) # q1.ln1.cfg_fpcs
upar_write_driver(0x908628,0x00000106) # q1.ln1.cfg_tx_GearFIFO
upar_write_driver(0x90862c,0x0008020C) # q1.ln1.cfg_tx_GearFIFO
upar_write_driver(0x908608,0x0000101A) # q1.ln1.cfg_rx_GearFIFO
upar_write_driver(0x90860c,0x0008020C) # q1.ln1.cfg_rx_GearFIFO
upar_write_driver(0x909210,0x0000017C) # q1.ln1.cfg_word_align_comma
upar_write_driver(0x909214,0x000003FF) # q1.ln1.cfg_word_align_comma
upar_write_driver(0x909220,0x0008017C) # q1.ln1.cfg_bonding
upar_write_driver(0x909288,0x00000000) # q1.ln1.cfg_bonding
upar_write_driver(0x90927c,0x0000007C) # q1.ln1.cfg_bonding
upar_write_driver(0x909280,0x0000007C) # q1.ln1.cfg_bonding
upar_write_driver(0x909284,0x0000007C) # q1.ln1.cfg_bonding
upar_write_driver(0x90888c,0x00000100) # q1.ln1.cfg_bonding
upar_write_driver(0x90922c,0x0010017C) # q1.ln1.cfg_ctc
upar_write_driver(0x909278,0x0000007C) # q1.ln1.cfg_ctc
upar_write_driver(0x90923c,0x00000008) # q1.ln1.cfg_tx_data_manipulation
upar_write_driver(0x909208,0x00000008) # q1.ln1.cfg_rx_data_manipulation
upar_write_driver(0x90882c,0x00707120) # q1.release_ln
upar_write_driver(0x908840,0x00707120) # q1.release_ln
upar_write_driver(0x908854,0x00707120) # q1.release_ln
upar_write_driver(0x908868,0x00707120) # q1.release_ln
""",
}

_serdes_toml = """\
# GW5AST-138B Q1 lane {lane}: 100 MHz REFCLK1, 1.25 Gb/s, raw 10-bit PCS.
# Source for the embedded CSR configuration; see doc/gw5_1000basex.md for regeneration.
device = "GW5AST-138"

[q0]
por_toggle_by_fabric = false
pd_toggle_by_fabric = false
ref_pad0_freq = "0M"
quad_clk_to_mac_sel = "CM0"
mac_quad_clk_sel = "Q0"
enable = false
lane_reset_by_fabric = true
ref_pad1_freq = "0M"
rx_quad_clk_internal_sel = "LN0_PMA_RX_CLK"
rx_quad_clk_sel = "Internal"
tx_quad_clk_internal_sel = "CM0"
tx_quad_clk_sel = "Internal"
refmux_scheme = "USER_DEFINED"
refimux0_sel = 0
refimux1_sel = 0
ref_prop_dir = 1
refomux0_sel = 0
qpll0_ref_sel = 0
qpll1_ref_sel = 0

[q0.ln0]
locked_from_fabric = false
chbond_enable = false
chbond_mst_sel = "q0.ln0"
ctc_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q0.ln0"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
chbond_clk_src = "lane"
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_data_rate = "1.25G"
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_data_rate = "1.25G"
cpll_ref_sel = 0

[q0.ln1]
locked_from_fabric = false
chbond_enable = false
chbond_mst_sel = "q0.ln1"
ctc_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q0.ln1"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
chbond_clk_src = "lane"
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_data_rate = "1.25G"
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_data_rate = "1.25G"
cpll_ref_sel = 0

[q0.ln2]
locked_from_fabric = false
chbond_enable = false
chbond_mst_sel = "q0.ln2"
ctc_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q0.ln2"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
chbond_clk_src = "lane"
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_data_rate = "1.25G"
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_data_rate = "1.25G"
cpll_ref_sel = 0

[q0.ln3]
locked_from_fabric = false
chbond_enable = false
chbond_mst_sel = "q0.ln3"
ctc_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q0.ln3"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
chbond_clk_src = "lane"
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_data_rate = "1.25G"
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_data_rate = "1.25G"
cpll_ref_sel = 0

[q1]
por_toggle_by_fabric = false
pd_toggle_by_fabric = false
enable = true
quad_clk_to_mac_sel = "CM0"
mac_quad_clk_sel = "Q0"
lane_reset_by_fabric = true
ref_pad0_freq = "0M"
ref_pad1_freq = "100M"
rx_quad_clk_internal_sel = "LN{lane}_PMA_RX_CLK"
rx_quad_clk_sel = "Internal"
tx_quad_clk_internal_sel = "CM0"
tx_quad_clk_sel = "Internal"
refmux_scheme = "USER_DEFINED"
refimux0_sel = 3
refimux1_sel = 0
ref_prop_dir = 1
refomux0_sel = 0
qpll0_ref_sel = 0
qpll1_ref_sel = 0

[q1.ln{lane}]
locked_from_fabric = false
chbond_trigger_by_fabric = true
enable = true
tx_data_rate = "1.25G"
rx_data_rate = "1.25G"
pcs_tx_clk_src = 0
loopBack = "OFF"
width_mode = 10
tx_gear_rate = "1:1"
rx_gear_rate = "1:1"
encode_mode = "OFF"
decode_mode = "OFF"
word_align_enable = true
comma = "K28.5"
comma_mask = "1111111111"
chbond_enable = false
chbond_align_length = 0
chbond_align_pattern0 = 124
chbond_align_pattern1 = 124
chbond_align_pattern2 = 124
chbond_align_pattern3 = 124
chbond_align_pattern1_is_kcode = false
chbond_align_pattern2_is_kcode = false
chbond_align_pattern3_is_kcode = false
chbond_max_skew = 8
chbond_clk_src = "lane"
ctc_enable = false
ctc_skipb_pattern_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_skipa_pattern = 124
ctc_skipb_pattern = 124
ctc_skipb_pattern_is_kcode = false
ctc_rd_start_depth = "16"
ffe_manual = false
sr_sd_thsel = 6
chbond_mst_sel = "q1.ln{lane}"
ctc_mst_sel = "q1.ln{lane}"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
rx_bit_invert = false
rx_byte_invert = false
rx_data_manipulation_enable = false
rx_if_cfg_rd_start_depth = 8
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_pol_invert = false
rx_slip_distance = 8
tx_bit_invert = false
tx_byte_invert = false
tx_data_manipulation_enable = false
tx_if_cfg_mst_sel = "q1.ln{lane}"
tx_if_cfg_rd_start_depth = 8
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_pol_invert = false
tx_slip_distance = 8
cpll_ref_sel = 0

[q1.ln{other_lane}]
locked_from_fabric = false
chbond_trigger_by_fabric = true
enable = false
tx_data_rate = "1.25G"
rx_data_rate = "1.25G"
pcs_tx_clk_src = 0
loopBack = "OFF"
width_mode = 10
tx_gear_rate = "1:1"
rx_gear_rate = "1:1"
encode_mode = "OFF"
decode_mode = "OFF"
word_align_enable = true
comma = "K28.5"
comma_mask = "1111111111"
chbond_enable = false
chbond_align_length = 0
chbond_align_pattern0 = 124
chbond_align_pattern1 = 124
chbond_align_pattern2 = 124
chbond_align_pattern3 = 124
chbond_align_pattern1_is_kcode = false
chbond_align_pattern2_is_kcode = false
chbond_align_pattern3_is_kcode = false
chbond_max_skew = 8
chbond_clk_src = "lane"
ctc_enable = false
ctc_skipb_pattern_enable = false
ctc_clk_src = "fabric_c2i_clk"
ctc_skipa_pattern = 124
ctc_skipb_pattern = 124
ctc_skipb_pattern_is_kcode = false
ctc_rd_start_depth = "16"
ffe_manual = false
chbond_mst_sel = "q1.ln{other_lane}"
ctc_mst_sel = "q1.ln{other_lane}"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
rx_bit_invert = false
rx_byte_invert = false
rx_data_manipulation_enable = false
rx_if_cfg_rd_start_depth = 8
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_pol_invert = false
rx_slip_distance = 8
tx_bit_invert = false
tx_byte_invert = false
tx_data_manipulation_enable = false
tx_if_cfg_mst_sel = "q1.ln{other_lane}"
tx_if_cfg_rd_start_depth = 8
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_pol_invert = false
tx_slip_distance = 8
cpll_ref_sel = 0

[q1.ln2]
locked_from_fabric = false
chbond_trigger_by_fabric = true
chbond_align_pattern1 = 124
chbond_align_pattern1_is_kcode = false
chbond_align_pattern2 = 124
chbond_align_pattern2_is_kcode = false
chbond_align_pattern3 = 124
chbond_align_pattern3_is_kcode = false
decode_mode = "OFF"
encode_mode = "OFF"
chbond_enable = false
chbond_align_length = 0
chbond_align_pattern0 = 124
chbond_mst_sel = "q1.ln2"
chbond_max_skew = 8
ctc_enable = false
ctc_skipb_pattern = 28
ctc_skipb_pattern_is_kcode = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q1.ln2"
ctc_skipa_pattern = 28
ctc_rd_start_depth = "8"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
rx_bit_invert = false
chbond_clk_src = "lane"
rx_byte_invert = false
rx_data_manipulation_enable = false
rx_gear_rate = "1:1"
rx_if_cfg_rd_start_depth = 8
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_pol_invert = false
rx_data_rate = "1.25G"
rx_slip_distance = 8
ctc_skipb_pattern_enable = false
tx_bit_invert = false
tx_byte_invert = false
tx_data_manipulation_enable = false
ffe_manual = false
tx_gear_rate = "1:1"
tx_if_cfg_mst_sel = "q1.ln2"
tx_if_cfg_rd_start_depth = 8
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_pol_invert = false
tx_data_rate = "1.25G"
tx_slip_distance = 8
word_align_enable = true
comma = "K28.5"
comma_mask = "1111111111"
cpll_ref_sel = 0

[q1.ln3]
locked_from_fabric = false
chbond_trigger_by_fabric = true
chbond_align_pattern1 = 124
chbond_align_pattern1_is_kcode = false
chbond_align_pattern2 = 124
chbond_align_pattern2_is_kcode = false
chbond_align_pattern3 = 124
chbond_align_pattern3_is_kcode = false
decode_mode = "OFF"
encode_mode = "OFF"
chbond_enable = false
chbond_align_length = 0
chbond_align_pattern0 = 124
chbond_mst_sel = "q1.ln3"
chbond_max_skew = 8
ctc_enable = false
ctc_skipb_pattern = 28
ctc_skipb_pattern_is_kcode = false
ctc_clk_src = "fabric_c2i_clk"
ctc_mst_sel = "q1.ln3"
ctc_skipa_pattern = 28
ctc_rd_start_depth = "8"
enable = false
loopBack = "OFF"
pcs_rx_reset_by_fabric = true
pcs_tx_reset_by_fabric = true
pcs_tx_clk_src = 0
width_mode = 10
rx_bit_invert = false
chbond_clk_src = "lane"
rx_byte_invert = false
rx_data_manipulation_enable = false
rx_gear_rate = "1:1"
rx_if_cfg_rd_start_depth = 8
rx_ovs_mode = "OFF"
rx_ovs_pll_src = "N/A"
rx_ovs_ratio = "N/A"
rx_pol_invert = false
rx_data_rate = "1.25G"
rx_slip_distance = 8
ctc_skipb_pattern_enable = false
tx_bit_invert = false
tx_byte_invert = false
tx_data_manipulation_enable = false
ffe_manual = false
tx_gear_rate = "1:1"
tx_if_cfg_mst_sel = "q1.ln3"
tx_if_cfg_rd_start_depth = 8
tx_ovs_mode = "OFF"
tx_ovs_ratio = "N/A"
tx_pol_invert = false
tx_data_rate = "1.25G"
tx_slip_distance = 8
word_align_enable = true
comma = "K28.5"
comma_mask = "1111111111"
cpll_ref_sel = 0
"""
