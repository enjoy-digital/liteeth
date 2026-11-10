# Sources of the manual's facts

Every number and name in the manual is taken from the repository at the documented revision. This
file records where each chapter's facts come from, so that a reviewer can re-check them and a
maintainer knows what to update when the code changes.

| Chapter / section | Sources |
|---|---|
| 00 Front matter | `doc/manual/manual.yaml`, `README.md` |
| 01 Introduction | `README.md`, `LICENSE`, `CONTRIBUTORS`, `liteeth/phy/` (PHY classes), `liteeth/mac/__init__.py`, `bench/colorlight_5a_75b.py` |
| 02 Ethernet primer | `liteeth/common.py`, `liteeth/mac/{preamble,padding,crc,gap}.py`, `liteeth/core/ip.py`, `bench/kc705.py`, LiteX `bios/cmds/cmd_liteeth.c`; IEEE 802.3 |
| 03 PHY interfaces | `liteeth/phy/common.py`, `liteeth/phy/parallel/{mii,rmii,gmii,gmii_mii,xgmii}.py`, `liteeth/phy/parallel/rgmii/{s7,ecp5}.py`, `liteeth/phy/serial/basex/pcs.py`, `liteeth/phy/serial/baser/pcs/{common,scrambler,block_sync,ber_mon,encoder}.py`, `liteeth/phy/serial/baset.py`, `test/model/pcs_100g.py`; IEEE 802.3 |
| 04 Network primer | `liteeth/common.py`, `liteeth/core/{arp,ip,icmp,udp,dhcp,igmp}.py`, `liteeth/core/ptp/{common,__init__,control}.py`, `liteeth/frontend/etherbone.py`, LiteX `soc.py` (`add_etherbone`); RFC 768, 791, 792, 826, 2131, 2236; IEEE 1588 |
| 05 Architecture | `liteeth/core/__init__.py`, `liteeth/common.py`, `liteeth/crossbar.py`, `liteeth/fifo.py`, `liteeth/mac/core.py`, LiteX `soc.py` (`add_etherbone`), `doc/packet_contracts.md`, `doc/stream_byte_enables.md`, `doc/high_speed_datapaths.md` |
| 06 MAC | `liteeth/mac/*.py`, `doc/mac_storage.md`, LiteX `soc.py` (`add_ethernet`, `add_etherbone`) |
| 07 IP stack | `liteeth/core/{arp,ip,icmp,udp,dhcp,igmp}.py`, `doc/ipv4_receive.md`, `doc/high_speed_datapaths.md`, `bench/udp_throughput.py` |
| 08 Frontends | `liteeth/frontend/{etherbone,stream}.py`, `liteeth/gen.py` (`UDPCore`), `bench/{test_etherbone,test_udp_streamer,arty}.py`, `liteeth/software/dissector/etherbone.lua`, LiteX `litex_server.py`; captures: LiteX history (`--udp-scan`), LiteX-M2SDR documentation (Etherbone bursts) |
| 09 PTP | `liteeth/core/ptp/*.py`, `doc/ptp.md`, `bench/{arty_ptp,acorn_baseboard_mini_ptp,test_ptp}.py` |
| 10 Parallel PHYs | `liteeth/phy/__init__.py`, `liteeth/phy/common.py`, `liteeth/phy/parallel/*.py`, `liteeth/phy/parallel/rgmii/*.py`, `liteeth/phy/serial/baset.py`, `liteeth/phy/simulation/model.py`, `bench/arty_10baset.py`, `doc/phy_layout.md`, `liteeth/gen.py` |
| 11 BASE-X PHYs | `liteeth/phy/serial/basex/{pcs,pcs32}.py`, `liteeth/phy/serial/basex/pma/*.py`, `liteeth/phy/serial/basex/wrappers/*.py`, `liteeth/phy/serial/gtp_7series.py`, `liteeth/phy/serial/gw5_serdes.py`, `doc/phy_portability.md`, `doc/gw5_1000basex.md`, `liteeth/gen.py`; capture: LiteX-Boards history |
| 12 BASE-R PHYs | `liteeth/phy/serial/baser/{pcs,pma,wrappers}/*.py`, `liteeth/phy/parallel/xgmii.py`, `liteeth/mac/axis.py`, `liteeth/mac/core.py`, `test/model/pcs_100g.py`, `bench/{alibaba_10g,ac701_5g,acorn_baseboard_mini_5g,cmac_100g,mac_100g}.py`, `doc/{baser,gw5_10gbaser,a7_5g,phy_portability,100g,100g_pcs}.md` |
| 13 Boards | `README.md`, `liteeth/phy/__init__.py`, `bench/*.py`, `doc/{a7_5g,gw5_1000basex,gw5_10gbaser}.md`, `liteeth/phy/serial/basex/pcs.py`; LiteX-Boards targets (`sipeed_tang_mega_138k_pro`, `alibaba_xcku3p`, `litex_acorn_baseboard`, `litex_acorn_baseboard_mini`, `digilent_arty`); LiteX WR-NIC `doc/boards.md` |
| 14 Getting started | `bench/{arty,test_etherbone,test_udp_streamer}.py`, `setup.py`, `liteeth/core/ip.py`; LiteX `litex_setup.py`, `litex_server.py`, `bios/{boot.c,cmds/cmd_liteeth.c}`, `libliteeth/udp.c`; LiteX-Boards `digilent_arty.py` |
| 15 Integration | `liteeth/gen.py`, `examples/*.yml`, `doc/gen.md`, `doc/phy_portability.md`, `liteeth/mac/core.py`, `liteeth/core/{__init__,udp,dhcp}.py`, `liteeth/frontend/{stream,etherbone}.py`, `bench/{arty,ac701_5g}.py`; LiteX `soc.py` (`add_ethernet`, `add_ethernet_timing_constraints`, `add_etherbone`), `bios/{boot.c,main.c,cmds/cmd_liteeth.c}` |
| 16 Examples and targets | `examples/*.yml`, `liteeth/gen.py` (`get_udp_port_ios`, `add_streamer_port`: `udp_port` key, `dhcp_mac_address` not connected), `bench/*.py` targets (CRG clocks, PHY arguments, `add_etherbone` buffer depths, SRAM, argument parsers; `butterstick.py` analyzer probes, `arty_ptp.py` and `acorn_baseboard_mini_ptp.py` `monitor_debug`), `bench/arty_10baset.py` (wiring comment), `bench/test_etherbone.py`, `bench/test_udp_streamer.py`, `bench/test_ptp.py` (`monitor_*` registers), `liteeth/core/ptp/__init__.py` (`LiteEthPTP` parameters), `doc/a7_5g.md` (clocks, SFP host mode, PHY reset), `doc/100g.md` (CMAC build), `bench/cmac_100g.py` |
| 17 Simulation and verification | `test/model/*.py`, `test/stream_helpers.py`, `test/rtl_helpers.py`, `test/test_*.py` (counts, `skipUnless`), `test/test_udp.py` (DUT), `test/test_mac_100g.py`, `test/phy_basex_snapshot.py`, `.github/workflows/ci.yml`, `bench/sim.py`, `bench/dhcp_tap.py`, `bench/mac_100g.py`, `bench/udp_throughput.py`, `doc/100g.md`, `doc/high_speed_datapaths.md`, `doc/phy_portability.md` (snapshot comparison), LiteX `litex/tools/litex_sim.py` (Ethernet options) and `litex/build/sim/core/modules/ethernet/ethernet.c` (TAP setup) |
| 18 Debugging | `AGENTS.md` (method), `liteeth/phy/serial/baser/wrappers/diagnostics.py` (status fields, read-to-clear, PRBS snapshot), `liteeth/phy/serial/baser/wrappers/usp_gt.py` (loopback values), `doc/phy_portability.md` ("Reading BASE-R Diagnostics"), `doc/gw5_10gbaser.md` (GW5 status), `liteeth/phy/serial/basex/pcs.py` (`sgmii`, `autocsr_exclude`), `liteeth/phy/parallel/mii.py`, `rgmii/ecp5.py`, `rgmii/s7.py` (reset, delays, in-band status), `liteeth/phy/common.py` (MDIO), `liteeth/mac/core.py` (counters), `liteeth/mac/gap.py`, `liteeth/core/arp.py` (`clear_enable`), `liteeth/frontend/stream.py` (`with_broadcast` filter), `liteeth/software/dissector/etherbone.lua`, `bench/*.py` (bridges, `sfp_tx_disable_n`), LiteX `bios/cmds/cmd_liteeth.c` (`mdio_*`, `sfp`), `litex/tools/litex_server.py` (`--udp-scan`), LiteScope `litescope_cli` options, LiteX commit `b02753ecf` (scan capture), LiteX `soc.py` `add_etherbone` (clock domain, Etherbone without MAC registers) |
| A Register reference | Generated by `tools/csr_doc.py` from `registers.py`: `liteeth/gen.py` with `examples/wishbone_mii.yml`, `bench/colorlight_5a_75b.py`, `bench/arty_10baset.py` and the two manual-only SoCs of `registers.py` (snapshots in `csr/`); CSR descriptions in `liteeth/mac/sram.py`, `mac/core.py`, `phy/common.py`, `phy/parallel/mii.py`, `phy/parallel/rgmii/ecp5.py`, `phy/serial/baset.py`, `phy/serial/basex/pcs.py`, `phy/serial/baser/wrappers/diagnostics.py`, `core/ptp/__init__.py` |
| B Build options | `liteeth/gen.py` (keys and defaults, accepted PHYs, `main`), `liteeth/mac/__init__.py` (`LiteEthMAC`), `liteeth/core/__init__.py` (`LiteEthIPCore`, `LiteEthUDPIPCore`), `liteeth/frontend/etherbone.py`, `frontend/stream.py`, `core/udp.py` (`get_port`), `core/dhcp.py`, `core/ptp/__init__.py`, phy wrapper defaults (`basex/wrappers/k7_gtx.py`, `usp_gty.py`, `baser/wrappers/usp_gt.py`), argument parsers of `bench/*.py`, LiteX `soc.py` (`add_ethernet`, `add_etherbone`) and `litex_sim.py` |
| C Glossary | Chapters 1 to 18, `liteeth/common.py` (sizes), `liteeth/core/ip.py` (TTL) |
| D Performance and resources | `liteeth/common.py` (frame sizes), IEEE 802.3 frame format (computed tables), `examples/*.yml` and `bench/*.py` (widths and clocks), `doc/high_speed_datapaths.md` (cycle and WNS tables), `doc/100g.md` (timing and resources), `test/test_mac_100g.py`, LiteX-M2SDR public repository: `doc/sata-validation.md` (Etherbone burst table), `doc/notes/sata-bandwidth-investigation.md`, `doc/debugging-guide.md` (UDP loopback capture) |
| E Maintaining this manual | Manual kit (`tools/kit.py`, `KIT`), `.github/workflows/doc.yml` |

## Facts that drift {#sources-drift}

Some facts of the manual change with the code more often than others. Re-check them when the files
below change.

| Fact | Where it is stated | Source to re-check |
|---|---|---|
| Hardware status per PHY and family | 1, 13 | Maintainer decisions, `README.md` |
| PHYs exported by `liteeth.phy` and accepted by the generator | 13, 15, B | `liteeth/phy/__init__.py`, `liteeth/gen.py` |
| Generator keys and defaults | 15, B | `liteeth/gen.py`, `examples/*.yml` |
| Bench targets, their options and the known issues | 16, 18, B | `bench/*.py`, `bench/test_*.py` |
| Register banks | A | `csr_doc.py dump --check` fails when a snapshot is stale |
| Test counts and CI matrix | 17 | `test/`, `.github/workflows/ci.yml` |
| Recorded measurements | D | `doc/high_speed_datapaths.md`, `doc/100g.md` |

The PHY tree keeps its established module names as import aliases (`liteeth/phy/__init__.py`);
the developer notes `doc/phy_layout.md` and `doc/phy_portability.md` give the compatibility rules
to follow when PHY files move.
