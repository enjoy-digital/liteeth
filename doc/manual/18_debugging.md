# Debugging {#debugging}

This chapter explains how to find why a LiteEth design does not answer, or answers badly, on a
board. It gives the method, the checks of the host side, the checks for a link that does not come
up, for a link that comes up without traffic, and for slow traffic, then the known issues. Read it
before a long investigation: most problems are found with a few register reads.

## Method {#debug-method}

The approach is to observe before capturing. The host already sees much of the exchange, the PHY
and MAC registers answer most questions about the link, and a logic analyzer capture is only worth
taking once the question is narrow.

1. Check the connection and the design: the host reaches the board, and the identifier matches
   the build.
2. Check the host side: address, route, ARP, and what the host sends and receives.
3. Read the registers: PHY status, MAC counters, MDIO registers of the external PHY.
4. Capture a narrow LiteScope trace only when the question is about timing inside the FPGA.
5. Reproduce the finding in simulation, fix it there, then check again on the board
   ([Simulation and verification](#simulation)).

An Etherbone design is debugged over the link it debugs: when the link is down, it has no
register access at all. Keep a second bridge in a design under bring-up, JTAGBone or UARTBone
(`add_jtagbone`, `add_uartbone`), as the `arty_10baset`, `butterstick`, `alibaba_10g` and
Artix-7 5G targets do. The host then reaches the registers with `litex_server --jtag` or
`litex_server --uart` whatever the state of the link.

## Connection and identity {#debug-connect}

Start from the host with the LiteX defaults: the board at 192.168.1.50, the host at
192.168.1.100. The host needs an address in the same subnet on the interface cabled to the board.

```sh
# Host address on the interface connected to the board (replace eth0).
sudo ip addr add 192.168.1.100/24 dev eth0
ping 192.168.1.50
litex_server --udp --udp-ip 192.168.1.50 &
litex_cli --ident
```

Look for an identifier that names the target and the build date. `litex_cli` and the host tools
need the `csr.csv` of the build for register names; a stale `csr.csv` from another build gives
wrong addresses without an error. `litex_server --udp --udp-scan --udp-ip 192.168.1.x` lists the
Etherbone devices of a subnet; [Step 5 of Getting started](#gs-server) shows its output on a bench
with two boards.

## The host side {#debug-host}

A host with several interfaces may route 192.168.1.50 through the wrong one: check with
`ip route get 192.168.1.50`. A firewall on the host can drop the UDP answers of the board, and a
second device with the same address answers in its place. Unplug the other boards of the subnet
while debugging one.

The ARP table of the host tells whether the board answers at the Ethernet level, before any IP
traffic. An incomplete or failed entry after a ping means that the ARP requests get no answer.

```sh
ip neigh show 192.168.1.50
sudo tcpdump -i eth0 -e -n host 192.168.1.50
```

`tcpdump -e` prints the MAC addresses of every frame: look for the ARP request of the host, the
reply of the board with its MAC address (`10:e2:d5:00:00:00` by default), then the ICMP or UDP
exchange. A frame the board sends but the host never shows was dropped before the host stack,
often for a bad FCS: the statistics of the network interface count them (`ip -s link show eth0`).

### Wireshark and the Etherbone dissector {#debug-wireshark}

`liteeth/software/dissector/etherbone.lua` decodes Etherbone packets in Wireshark: probes,
records, addresses and data. It registers itself on UDP port 1234, the default Etherbone port.

```sh
wireshark -X lua_script:liteeth/software/dissector/etherbone.lua -i eth0
```

Each `litex_server` access appears as one request and one reply. A request without a reply points
to the board (record rejected, bus access stalled); a reply the host tool does not accept points
to the host side.

## No link {#debug-no-link}

A link that does not come up is a PHY problem. Read the status of the PHY first, through the
second bridge, then go down the list of its family.

| PHY family | Status to read | Usual causes |
|----------------------------|----------------------------------------|----------------------------------------------------|
| MII, RMII, GMII, RGMII | MDIO registers, RGMII in-band status | External PHY in reset, strapping, RGMII delays |
| 10BASE-T on IOs | `ethphy_status` | Wiring, cable crossing, receive termination |
| 1000BASE-X, 2500BASE-X | `link_up` of the PHY, PCS status | Reference clock, polarity, SFP, autonegotiation |
| 5G, 10G, 25G BASE-R | `ethphy_status` | Reference clock, polarity, SFP host mode, FEC |

The register names and fields are in [Register reference](#registers).

### Parallel PHYs and the external PHY {#debug-parallel}

The external PHY holds the link: its registers say whether it sees a partner. The BIOS reads them
over MDIO, and so can a host script through the `mdio_w` and `mdio_r` registers of the PHY.

```text
mdio_dump <phyadr> <count>
mdio_read <phyadr> <reg>
```

Register 1 of Clause 22 is the basic status register; its bit 2 is the link status. The link
bit latches low: read register 1 twice, the second read gives the present state. The
autonegotiation result is in the vendor registers of the external PHY. A PHY address that returns
`0xffff` everywhere is the wrong address, or an external PHY held in reset.

- **Reset.** `ethphy_crg_reset` holds the external PHY in reset while set, through its reset pin
  when the board has one. Some external PHYs need a reset pulse at power-up: keep
  `with_hw_init_reset=True`, the class default (the bench targets set it to False), or set
  `phy_hw_init_reset: True` in the generator, whose default is False.
- **RGMII delays.** RGMII needs a delay of about 2 ns between clock and data in each direction,
  added once: by the external PHY or by the FPGA, never both and never neither. `tx_delay` and
  `rx_delay` set the FPGA side; a board whose external PHY adds the transmit delay uses
  `tx_delay=0`, as the ECP5 targets do. Wrong delays give a link with CRC errors or no traffic.
- **ECP5 RGMII.** `ethphy_rx_inband_status` shows the link, speed and duplex that the external PHY
  reports between frames. A speed other than 1000 Mbps needs the experimental
  `with_dynamic_link` option.

### BASE-X and BASE-R PHYs {#debug-serial}

- **Reference clock.** The frequency passed to the PHY (`refclk_freq`) must be the one on the
  board; the PHY computes its PLL settings from it. A wrong reference gives a PLL that does not
  lock, or a link at the wrong rate. Check the clock generator of the board, which the BIOS
  programs on some boards (AC701 Si5324).
- **Polarity.** Boards that swap a differential pair need `tx_polarity` or `rx_polarity`. The
  Acorn module inverts its receive pair; on the Acorn Baseboard Mini, the two transmit inversions
  cancel.
- **SFP module.** The module transmits only when its TX_DISABLE input is low. Targets drive it
  (`sfp_tx_disable_n`); on the AC701, that platform signal is in fact an active-high TX_DISABLE.
  A multi-rate module may need its host interface set (`sfp` BIOS command,
  [Examples and targets](#examples)).
- **Autonegotiation (BASE-X).** The PCS runs the Clause 37 autonegotiation and detects SGMII from
  the configuration word of the partner (`sgmii=None`). An SGMII external PHY that answers only to
  SGMII words needs `sgmii=True` on the PCS (`pcs_kwargs` of the wrappers that take it).
  `pcs_status.config_reg` shows the last word of the partner when the PCS registers are built
  ([Register reference](#reg-ethphy-basex)).
- **FEC (BASE-R).** The BASE-R PHYs have no FEC: the link partner must run without FEC. Switches
  and NICs often enable it by default at 25G.

### BASE-R diagnostics {#debug-baser}

The BASE-R PHYs built with `with_csr=True` have four registers: `ethphy_reset`, `ethphy_control`,
`ethphy_status` and `ethphy_rx_prbs_errors` ([Register reference](#reg-ethphy-baser)). They
answer the three questions of a serial link in order: does the receiver find the blocks, are they
clean, and is the transmit side sound.

| `status` bit | Meaning | When it is wrong |
|--------------------------------|-------------------------------------------|-------------------------------------|
| `block_lock` | The receiver found the 64b/66b block boundaries | No signal, wrong rate or reference clock |
| `high_ber` | Too many invalid sync headers in the window | Bad channel, wrong polarity, FEC partner |
| `link_up` | Block lock and no high BER | Receive side only, not a full link |
| `block_lock_lost` | Lock lost since the last read of `status` | Intermittent channel |
| `high_ber_latched` | High BER since the last read of `status` | Intermittent errors |

Reading `status` clears the two sticky bits, so read it twice: the second read tells the present
state. `link_up` is the receive status of the PCS, as Clause 49 defines it: it does not prove that
the partner receives the frames of the FPGA. Check traffic in both directions, after a reset and
after a cable reconnection.

The transceiver loopbacks isolate the FPGA from the channel. `control.loopback` selects the
loopback of the transceiver (on UltraScale+: 1 near-end PCS, 2 near-end PMA, 4 far-end PMA, 6
far-end PCS). With a near-end loopback, a design that gets `block_lock` has a sound reference
clock and transceiver; the problem is then on the channel or the partner.

The PRBS31 generator and checker test the raw channel, with a partner or a loopback in PRBS mode.
`rx_prbs_errors` counts the bit errors and saturates; reading a counter wider than the bus needs
a frozen snapshot:

1. Clear `control.prbs_pause` and wait for `status.prbs_paused` to read 0.
2. Enable the checker (`control.rx_prbs31_enable`) and leave it on during the measurement.
3. Set `control.prbs_pause` and wait for `status.prbs_paused` to read 1.
4. Read `rx_prbs_errors`.
5. Clear `control.prbs_pause` and wait for `status.prbs_paused` to read 0 before the next snapshot.

Disabling the checker or resetting its receive side clears the counter. Use a timeout when
polling: the receive clock may stop. The bit error ratio is the error count divided by the
elapsed time and the line rate. The GW5 10GBASE-R PHY builds the PRBS logic only with
`with_prbs=True`, has no loopback control, and adds `ethphy_serdes_status` for the PLL and CDR
lock of the transceiver.

## Link up, no traffic {#debug-no-traffic}

With a link up and no answer to `ping`, follow the frame through the layers. [ARP, IP, ICMP, UDP,
DHCP and IGMP](#ip-stack) describes what each layer accepts.

1. **ARP.** No ARP reply in `tcpdump`: the board does not receive, or its address differs from
   the one the host asks for. Check the `ip_address` of the core and the MAC counters below.
2. **IP.** An ARP reply but no ping reply: the IPv4 layer of the core rejects the packet, or the
   ICMP layer drops it. Pings larger than the ICMP buffer (`icmp_fifo_depth`, 128 bytes by
   default) get no reply: test with `ping -s 56` first.
3. **Subnet and gateway.** A core built with `gateway_ip` and `netmask` sends to the gateway every
   packet outside its subnet; without them, it resolves every destination with ARP directly.
4. **UDP.** A UDP packet to a port no user port listens on is dropped silently. Check the
   destination port; a streamer built with `with_broadcast=False` also drops the packets of
   other sources than its `ip_address`.
5. **Hybrid mode.** The hardware stack receives the frames to its own MAC address and the
   broadcast and multicast frames; the CPU receives every frame except the unicast ones to the
   hardware MAC address. The two sides need different MAC and IP addresses, which
   `add_etherbone` checks.

The MAC counters tell whether frames reach the core intact. They exist as registers in the
software and hybrid MACs only: an Etherbone design without `with_ethmac` exports none.

The register names below start with `ethmac_`:

- `rx_datapath_crc_errors` counts the received frames with a bad FCS: signal integrity, or RGMII
  delays.
- `rx_datapath_preamble_errors` counts the frames without a valid preamble: clocking, or the
  alignment of the PHY.
- `rx_datapath_drops`, when the MAC stores whole frames (wide or fast datapaths), counts the
  frames dropped by the receive buffer: the core is slower than the line.
- `sram_writer_errors` counts the frames dropped for lack of a free receive slot: the CPU does
  not empty the slots in time.

## Slow or lossy traffic {#debug-slow}

The rate a design reaches depends on its slowest stage. Compare the line rate with the capacity of
each datapath, the width times the clock: an 8-bit core at 125 MHz carries 1 Gbps, a 64-bit core
at 156.25 MHz 10 Gbps. Short frames cost more: the per-packet work of the header path and the
gap between frames limit the packet rate. [Performance and resources](#performance) gives the
efficiency per frame size and line rate, and the recorded measurements.

- **Etherbone.** A read is a request and a reply over UDP, and the host tool waits for the reply:
  single reads are bound by the round trip, not by the link, and bursts of words help. Writes get
  no reply, so the host sends them without waiting. `test_etherbone.py --speed` uses bursts of 255
  words, which the Etherbone buffer of the target must hold.
- **Drops on receive.** The receive path cannot stop the link partner. A user port that does not
  take its data fills the buffers, and the core drops whole frames (counted in
  `rx_datapath_drops` when the MAC has that buffer).
- **Clock-domain crossing.** At 8 bits, `add_etherbone` runs the core in the receive clock domain
  of the PHY; user logic in `sys` goes through a crossing FIFO. Check where each part runs, as
  [Core architecture](#architecture) explains, before computing a limit.

## LiteScope on the streams {#debug-litescope}

LiteScope captures the streams inside the core when the question is about cycles: a frame stuck
between two layers, a ready never asserted, an error flag on a beat. Probe the `valid`, `ready`,
`last` and `error` signals of the stream boundaries first, the data later. The `butterstick`
target shows a probe list on the MAC, ARP, IP, UDP and Etherbone streams.

```python
from litescope import LiteScopeAnalyzer
self.analyzer = LiteScopeAnalyzer([
    self.ethcore_etherbone.mac.core.source.valid,
    self.ethcore_etherbone.mac.core.source.ready,
    self.ethcore_etherbone.ip.rx.sink.valid,
    self.ethcore_etherbone.udp.rx.sink.valid,
], depth=512, csr_csv="analyzer.csv")
```

```sh
# List the probed signals, then trigger on the rising edge of one, 32 samples before it.
litescope_cli --list
litescope_cli -r <signal> --offset 32
```

The analyzer samples in one clock domain (`clock_domain`, `sys` by default): probe each stream in
the domain where it runs. Read over Etherbone, a capture shares the link it observes; use the
second bridge when the capture concerns the receive path.

## Known issues {#debug-known}

These limitations of the code on this revision explain some symptoms. They are listed here so
that a reader does not chase them as board problems.

| Symptom | Cause |
|-----------------------------------------------|----------------------------------------------------------|
| `butterstick` target fails to build | LiteScope probe on a state machine that the Etherbone sender does not have |
| PTP targets and `test_ptp.py` fail | They pass `monitor_debug` and read `ptp_monitor_*` registers, which `LiteEthPTP` does not have |
| Generator DHCP requests with a zero MAC | The generator does not connect the client MAC address |
| Generator `data_width: 16` fails to elaborate | The generator puts a 16-bit datapath in `sys`, where the CRC stages take 8, 32 or 64 bits only |
| `udp_raw_ecp5rgmii` streamers with dynamic ports | The example uses `port:`, the generator reads `udp_port` |
| ARP requests after an idle second | The ARP cache is cleared after one second without lookup (`clear_enable`) |
| Short gap between frames on MII and RMII | The gap is counted in clock cycles of a byte stream |
| Extra idle between frames on BASE-R | The MAC adds its gap and the PHY its own idle insertion |
| No PCS status or link event on Kintex-7, Virtex-7, UltraScale(+) BASE-X | These wrappers build the PCS without its registers unless `pcs_kwargs` asks for them ([1000BASE-X, 2500BASE-X and SGMII](#basex-phys)) |

Where to go next: [Simulation and verification](#simulation) to reproduce a finding;
[Register reference](#registers) for every register named here.
