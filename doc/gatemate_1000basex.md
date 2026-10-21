# GateMate 1000BASE-X

`GateMate_1000BASEX` connects LiteEth's 1000BASE-X PCS and Clause 37
autonegotiation to the GateMate `CC_SERDES`. The SerDes performs the 8b/10b
coding and comma alignment and exchanges 4 code-groups per cycle: the PCS
runs with its 32-bit datapath (`PCS(dw=32)`, `PCSTX32b`/`PCSRX32b`) at
31.25 MHz, from the SerDes PLL output. SGMII is not supported.

```python
from liteeth.phy import GateMate_1000BASEX
ethphy = GateMate_1000BASEX(sys_clk_freq, refclk_freq=100e6)
```

`refclk_freq` is 100 MHz or 125 MHz. `tx_polarity`/`rx_polarity` invert the
serial lanes. Registers at the SerDes interface and buffers at the MAC
interface (`with_pcs_buffers=True`) keep the datapaths short. With
`with_csr=True`, `ethphy_reset` resets the SerDes and PCS and
`ethphy_pma_status` reports the reset sequencer state.

The PCS RX realigns the received code-groups on the last comma or /S/, so
ordered sets and packets can start on any lane of the 32-bit word. Invalid
code-groups or unexpected K-characters end the packet with an error.

Status: tested against a LiteEth 1000BASE-X link partner. The SerDes
configuration (CDR, equalizer, RX elastic buffer without clock correction)
still needs validation against other link partners: with an Intel 82599
SFP+ NIC, half of the received bytes were reported as zeros.
