# Native BASE-R PHYs

LiteEth's native 5G/10G/25G BASE-R path combines its Ethernet MAC, XGMII adapter,
64b/66b PCS and a device-specific PMA. The PCS stays in LiteEth; PLL, DRP and
applicable transceiver initialization helpers come from LiteICLink. Board targets
own pin placement, clock sources and timing constraints.

## Rates and clocks

| PHY | Serial rate | 64-bit PHY clock | PCS cadence |
| --- | --- | --- | --- |
| K7 GTX 10G | 10.3125 Gb/s | 161.1328125 MHz | Gearbox enables qualify blocks |
| USP GTH/GTY 10G | 10.3125 Gb/s | 156.25 MHz | One block per clock |
| USP GTY 25G | 25.78125 Gb/s | 390.625 MHz | One block per clock |

The existing 5G variants retain their corresponding half-rate clocks. A 25G GTY
requires QPLL0 with the 25G tuning in its PMA. Both the existing 156.25 MHz
fractional-N reference and 161.1328125 MHz integer-N reference are supported by
construction tests. This does not establish signal integrity or timing closure
on a new board. Shared QPLLs have exactly one reset owner; see
[PHY portability](phy_portability.md).

## Link capabilities

- Native encoding, scrambling, block lock, BER monitoring and PRBS31 are available.
- FEC is not implemented: configure the link partner for no FEC and select a
  medium/module that supports that operating mode. Neither BASE-R FEC nor RS-FEC
  can be enabled by changing a transceiver parameter.
- Speed is selected at construction. Autonegotiation and link training are not
  implemented; a raw serial rate alone does not establish CR/KR interoperability.
- The PCS emits Local Fault ordered sets when reception is not synchronized, and
  preserves received sequence ordered sets through its decoder. The XGMII adapter
  does not implement the full local/remote fault response state machine.
  `link_up` reports qualified PCS reception, not a negotiated bidirectional link.

The 10G BER monitor uses a 16-invalid-header threshold and a 125 us window. The
25G monitor uses 97 invalid headers in 2 ms, independently of the 125 us recovery
watchdog. These rate-specific values are described in the
[IEEE 802.3by BER analysis](https://www.ieee802.org/3/by/public/May15/ran_3by_01_0515.pdf)
and its [accepted Clause 107 comment](https://www.ieee802.org/3/by/public/comments/8023by_D01_comment_final_responses_by_clause.pdf).
The watchdog's 16-window qualification/restart policy is an implementation choice.
Loss of block lock or high BER overrides qualification even at a window boundary.

## Standalone generation

Run `python3 -m liteeth.gen examples/udp_baser.yml`. Select one of
`K7_GTX_5G_BASER`, `K7_GTX_10G_BASER`, `USP_GTH_5G_BASER`, `USP_GTH_10G_BASER`,
`USP_GTY_5G_BASER`, `USP_GTY_10G_BASER`, or `USP_GTY_25G_BASER` with `phy`.
Use `refclk_freq` for the dedicated differential reference clock (default
156.25 MHz), and `phy_tx_polarity`/`phy_rx_polarity` for lane polarity.

The generated core exposes `baser_refclk_p/n`, `baser_txp/txn`, `baser_rxp/rxn`,
`baser_rst`, and `baser_link_up`. Reset is a level request; link status is
synchronized to `sys`. Native PHY clock/reset handling stays in the wrapper.
The generator emits clock constraints but the integrating project must supply
pins, transceiver placement and the matching FPGA device. Fabric reference
clocks and FEC modes other than `phy_fec: none` are rejected explicitly.

The example uses a 64-bit 10G datapath. Selecting 25G changes the PHY rate; it
does not make a slower application or narrower stream sustain 25 Gb/s. Account
for the actual packet-processing clock, packet overhead and packet rate.

## Validation

Run the BASE-R generator, rate, reference-clock, shared-PLL and diagnostics tests,
plus `test/test_pcs_baser.py` and `test/test_xgmii_phy.py`. Tests cover BER threshold
boundaries/recovery, watchdog faults at qualification boundaries, primitive
selection, PRBS counter transfer, block synchronization, coding and packet paths.
They do not model analog CDR behavior, cable loss or transceiver calibration.

For each board/rate, build with its actual constraints, check timing, then exercise
PRBS, minimum/standard/jumbo frames, cable removal/reconnection and channel resets.
Test both channels of a shared PLL, including follower reset isolation. Record
FPGA part/speed grade, reference clock, module, FEC setting, packet loss and errors.

For wider application streams and a separate 25G generator example, see
[High-speed packet datapaths](high_speed_datapaths.md).
