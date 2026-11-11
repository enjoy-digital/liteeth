# Precision Time Protocol {#ptp}

This chapter describes the PTP core: a slave clock that follows a PTP master over UDP/IPv4 and
keeps a seconds and nanoseconds time in the FPGA. It covers the protocol exchanges it runs, the
time-stamping unit, the servo, its integration and its limits. The protocol is introduced in
[ARP, IP, UDP and Etherbone](#network-primer).

The PTP core is a supported feature with clear limits. It is a slave only, over UDP/IPv4 only, and
it takes its timestamps at the UDP user ports in the core's clock domain, not at the PHY; see
[Timestamp position](#ptp-timestamps). No accuracy figure is published for it.

## At a glance {#ptp-glance}

| Item | Value |
|---------------------|----------------------------------------------------------------------|
| Top-level class | `LiteEthPTP` (`liteeth/core/ptp/`) |
| Standard | IEEE 1588-2008 (PTP version 2) messages |
| Role | Slave only: no master, no boundary clock, no best master selection |
| Transport | UDP/IPv4; receives on the two ports given to it (319 and 320 by convention), sends from and to port 319; no Layer 2 transport |
| Delay mechanisms | End-to-end (E2E) or peer-to-peer (P2P) |
| Sync modes | One-step and two-step masters |
| Time | 48-bit seconds, 32-bit nanoseconds, addend with 20 fractional bits |
| Servo | Full phase correction per exchange, frequency integrator, outlier handling |
| Timestamps | RX: first beat at the event port; TX: first body byte, after the PTP header; both in the PTP clock domain |
| Registers | `locked`, `master_ip` |
| Clock domain | One, the domain of the module (often a copy of `sys`) |

## Organization {#ptp-organization}

The PTP core is a UDP user with two ports. The figure below shows its parts: receivers for the
event and general messages, a latch on the first beat of event messages, a transmitter for the
delay requests, a control state machine, the servo and the time-stamping unit (TSU).

![PTP slave. Follow an exchange: the control state machine collects the master's timestamps from
the receivers and its own from the TSU latches, then hands t1 to t4 to the servo, which corrects
the TSU.](ptp_blocks.svg)

| Module | Responsibility |
|------------------|--------------------------------------------------------------------|
| `common.py` | PTP header, message types, ports and multicast addresses |
| `clock.py` | TSU and the receive first-beat detector (`LiteEthPTPRxTimestamp`) |
| `packet.py` | Delay request transmitter, message receivers |
| `control.py` | Master tracking, E2E and P2P state machines (`LiteEthPTPControl`) |
| `servo.py` | Pipelined phase and frequency correction (`LiteEthPTPClockServo`) |
| `__init__.py` | `LiteEthPTP`: wiring, settings, registers |

The receivers parse the PTP header and the first 20 bytes of the body: the timestamp and the
requesting port identity. They keep messages of PTP version 2, of the configured domain and of a
supported type (Sync, Follow_Up, Delay_Resp, Pdelay_Resp, Pdelay_Resp_Follow_Up, Announce). A
receiver stuck in the middle of a message for 1024 cycles resets itself.

## Exchanges {#ptp-exchange}

### Master selection {#ptp-master}

The core does not run the best master clock algorithm. It follows the source of the last Announce
message received while waiting for a Sync, or the source of the first Sync when it knows no master
yet.

With `require_announce`, it only accepts Sync from a master learned through Announce. With
`announce_timeout` (seconds), it forgets the master when no Announce comes in time.

### End-to-end {#ptp-e2e}

In E2E mode, the default, each exchange gives the four timestamps of the delay request-response
mechanism:

1. The master sends Sync; the core latches **t2** when the message reaches its event port.
2. A one-step master puts **t1** in the Sync; a two-step master sends it in a Follow_Up with the
   same sequence number.
3. The core sends a Delay_Req and latches **t3** when the first byte after the 34-byte PTP header
   enters its packetizer.
4. The master answers with a Delay_Resp holding **t4**, for the same sequence number and the
   core's port identity (`clock_id`).

The servo then computes the offset from the master, ((t2 - t1) - (t4 - t3))/2, and the mean path
delay, ((t2 - t1) + (t4 - t3))/2. An exchange that lacks one of the four timestamps is not served:
the core clears `locked` and waits for the next Sync. A Sync that arrives during an exchange
restarts it. After each served exchange the core ignores the next Sync, which may have been queued
during the servo update, so it runs one exchange every two Sync intervals.

The Delay_Req goes to the PTP multicast address 224.0.1.129 by default, or to the master's own
address with `unicast_delay_req`.

### Peer-to-peer {#ptp-p2p}

With `p2p_mode`, the core measures the link delay with Pdelay_Req messages to 224.0.0.107 instead of
Delay_Req. It latches the send time of its Pdelay_Req and the arrival of the Pdelay_Resp. It takes
the peer's receive and send times from the Pdelay_Resp and, for a two-step peer, from the
Pdelay_Resp_Follow_Up.

The link delay is half of the round-trip time minus the peer's turnaround time, and the offset is
(t2 - t1) minus that delay. Unlike E2E, a P2P exchange is served without checking that every
timestamp was captured.

The core only sends peer delay requests: it does not answer the Pdelay_Req of its neighbours. It
accepts Pdelay_Resp messages only from the master it follows, so the master must be its direct
peer. Its Pdelay_Req is 44 bytes, without the 10 reserved bytes of the standard message.

## Time-stamping unit {#ptp-tsu}

The TSU keeps the time as 48-bit seconds and 32-bit nanoseconds and advances it every cycle of its
clock. The figure below shows how.

![TSU. Follow the increment: the addend times 10^9 feeds a fraction accumulator, whose whole
nanoseconds are added to the time, with a wrap at one second. Below, the servo writes the addend
and applies offsets and steps; the latches capture the time for the timestamps.](ptp_tsu.svg)

The addend is the length of one clock cycle in units of 2^-32 second, with 20 more fractional bits.
Its reset value comes from the `sys_clk_freq` argument, which must therefore be the real frequency
of the PTP clock domain.

The multiplication by 10^9 and the fraction accumulator are pipelined for timing, and the fraction
carries over, so that the time does not drift from rounding.

The servo changes the time in three ways:

- **Addend.** A new addend changes the rate of the time, which corrects the frequency.
- **Offset.** A signed number of nanoseconds is added in one cycle, with a carry or borrow into the
  seconds; this corrects the phase.
- **Step.** The time is loaded with a target value; this is used once, for the first lock.

Two latches capture the time: one for receive timestamps, one for transmit timestamps. The control
state machine copies each latch into a shadow register one cycle later, and moves it into t2, t3,
p1 or p4 when the message that owns it completes.

## Servo {#ptp-servo}

The servo turns the timestamps of an exchange into corrections. It is a pipeline of seven stages,
started when the control state machine has a complete exchange. The figure below shows the input
latch, the five computations and the three possible outcomes; the last stage applies the outcome to
the TSU.

![Servo pipeline. Follow the stages left to right, then the three outcomes: a good exchange
corrects the phase and the addend, a second-boundary artifact corrects the seconds, a large error
steps the time to the master.](ptp_servo.svg)

The stages compute:

- the time differences t2 - t1 and t4 - t3 (p4 - p1 and p3 - p2 in P2P) in signed nanoseconds, and whether the seconds of the
  timestamps are close enough to use;
- whether both differences are close to one second with opposite signs and a small delay: an
  artifact of a second boundary, corrected by one second instead of a phase step;
- the phase error and the path delay, for E2E or P2P;
- the frequency step: the phase error divided by a power of two set from the clock frequency,
  ignored up to 256 ns, and limited per exchange;
- the new addend, kept within one integer unit of the nominal addend.

| Outcome | Condition | Action on the TSU |
|----------------|------------------------------------------------------|---------------------------------|
| Good exchange | Usable timestamps, no artifact, small error | Offset by the full phase error, new addend |
| Seconds artifact | E2E only: both differences within 50 ms of 1 s, opposite signs, delay of 5 ms or less | Offset by plus or minus 1 s |
| Coarse error | Seconds more than 1 apart, or phase of 500 ms or more | Step to t1 plus the time elapsed since t2 (without the path delay; the next exchange corrects the rest) |

The phase correction is the whole error at each exchange (a proportional gain of 1), clamped below
one second. The frequency correction accumulates in a shadow copy of the addend, which the servo
writes to the TSU; the addend is never read back from the TSU, so nothing else enters the loop.

## Timestamp position {#ptp-timestamps}

Where a timestamp is taken sets the precision of a PTP clock. The core takes both of its own
timestamps in its clock domain, at the UDP user ports, not at the PHY:

- **Receive (t2, and the peer delay response).** The first beat of an event message accepted on
  the event port. The message has crossed the PHY, the MAC checks and clock crossing, the MAC
  store-and-forward FIFO when present, IP, UDP and the clock crossing of the port.
- **Transmit (t3, and the peer delay request).** The moment the packetizer accepts the first byte
  of the message body, after the 34-byte PTP header: about 34 cycles after the message starts on
  an 8-bit port. The message then crosses the port, the UDP and IP
  layers, the MAC with its optional packet FIFO and gap, and the PHY.

Both paths have a latency that the core does not compensate. The fixed part differs between receive
and transmit, and this asymmetry adds a constant error to the offset. The receive timestamp is
taken on the first byte of the message and the transmit timestamp after its header, so part of the
fixed asymmetry comes from the core itself.

The variable part (clock crossing phases, other traffic sharing the crossbars and the MAC, FIFO
fill) adds noise to each exchange. The core also ignores the correction field of the messages it
receives, so the corrections of transparent clocks on the path are not applied.

The achievable accuracy therefore depends on the PHY, the data width, the buffering and the
traffic of each design. Measure it on the target system, for example against the master's time with
a pulse-per-second output built from the TSU seconds.

## Settings and registers {#ptp-registers}

`LiteEthPTP(event_port, general_port, sys_clk_freq, timeout, announce_timeout, require_announce,
unicast_delay_req)` takes the two UDP ports and the frequency of its clock. The settings below are
signals, driven by user logic; the registers are the only status visible from software.

| Name | Kind | Default | Meaning |
|-------------------------------|-------------|--------------|----------------------------------------|
| `enable` | signal in | 1 | Run the protocol; 0 resets the state machine |
| `p2p_mode` | signal in | 0 | Peer-to-peer delay mechanism |
| `unicast_delay_req` | signal in | parameter | Send Delay_Req to the master's address |
| `require_announce` | signal in | parameter | Accept Sync only from an announced master |
| `clock_id` | signal in | 1 | 80-bit port identity: clock identity and port number |
| `domain` | signal in | 0 | PTP domain number |
| `tsu.seconds` | signal out | | Seconds of the local time |
| `tsu.nanoseconds` | signal out | | Nanoseconds of the local time |
| `timeout` | signal out | | Pulses every `timeout` seconds while unlocked |
| `locked` | register | | Last exchange served |
| `master_ip` | register | | IPv4 address of the master followed |

`locked` means that the last exchange was complete and corrected the time. It does not mean that
the offset is below a threshold: watch the time against a reference to judge convergence. It is
cleared when an exchange misses a timestamp, when the master changes or when its Announce times
out.

## Integration {#ptp-integration}

The core needs two 8-bit UDP ports in its own clock domain, an IP layer that accepts multicast,
and IGMP membership so that switches forward the PTP groups. The `with_ip_broadcast` default of the
core already accepts multicast; see [Destination policy](#stack-ip-broadcast). The bench target
`bench/arty_ptp.py` builds it as below; `sys_eth` is a clock domain of its CRG at the system
frequency.

```python
from liteeth.core.ptp import LiteEthPTP

# Core with IGMP reports for the PTP groups 224.0.1.129 and 224.0.1.130.
self.add_etherbone(phy=self.ethphy, buffer_depth=255,
    with_igmp     = True,
    igmp_groups   = [0xE0000181, 0xE0000182],
    igmp_interval = 2,
)
udp = self.ethcore_etherbone.udp

# PTP event and general ports, with a clock crossing to sys_eth.
ptp_event_port   = udp.crossbar.get_port(319, dw=8, cd="sys_eth")
ptp_general_port = udp.crossbar.get_port(320, dw=8, cd="sys_eth")

# PTP core in sys_eth, with a port identity derived from a MAC address.
self.ptp = ClockDomainsRenamer("sys_eth")(LiteEthPTP(
    ptp_event_port, ptp_general_port, sys_clk_freq))
self.comb += self.ptp.clock_id.eq((0x10e2d5000001 << 16) | 1)
```

For P2P, add the group 224.0.0.107 to `igmp_groups` and drive `p2p_mode` high. The two PTP bench
targets also pass a `monitor_debug` argument that `LiteEthPTP` does not accept, so they fail to
elaborate as they stand; the code above leaves it out.

## Host side {#ptp-host}

A Linux host runs `ptp4l` as the master. The configuration below comes from the comments of
`bench/acorn_baseboard_mini_ptp.py`: E2E, UDP/IPv4, two-step, software timestamping. Replace `eth0`
with the interface on the board's network.

```text
[global]
twoStepFlag            1
time_stamping          software
delay_mechanism        E2E
network_transport      UDPv4
domainNumber           0
logAnnounceInterval    1
logSyncInterval        0
logMinDelayReqInterval 0
udp_ttl                1

[eth0]
masterOnly             1
```

```sh
sudo ptp4l -f ptp-master.cfg
```

With Etherbone in the design, read the status from the host while `litex_server` runs: `locked`
should read 1 after the first exchanges and `master_ip` should hold the host address.

```python
from litex import RemoteClient

bus = RemoteClient(csr_csv="csr.csv")
bus.open()
print(bus.regs.ptp_locked.read(), hex(bus.regs.ptp_master_ip.read()))
bus.close()
```

`bench/test_ptp.py` reads a set of `ptp_monitor_*` registers that the core does not have, so it
does not work with the core as it is. A software-timestamping master also adds its own jitter
to every exchange.

## Limitations {#ptp-limits}

- Slave only: no master, no boundary clock, no best master clock algorithm; no answer to the
  Pdelay_Req of neighbours.
- UDP/IPv4 only, no Layer 2 transport; no unicast negotiation.
- Timestamps at the UDP user ports in the PTP clock domain, without latency compensation; the
  transmit timestamp is taken after the PTP header and the receive timestamp on the first byte.
  The correction field of received messages is ignored and sent as 0.
- The `udp_port` argument of the receivers is not used: the core receives on the ports given to
  `LiteEthPTP` and sends from and to port 319.
- One exchange every two Sync intervals: the Sync after a served exchange is ignored.
- P2P: Pdelay_Resp accepted only from the followed master, so no P2P transparent clock between the
  core and the master; a P2P exchange is served without checking that every timestamp was
  captured; the Pdelay_Req is 44 bytes instead of 54.
- No accuracy figure is published; `locked` reports served exchanges, not an offset bound.
- No pulse-per-second output and no time registers: user logic reads `tsu.seconds` and
  `tsu.nanoseconds` directly.
- The PTP bench targets and `bench/test_ptp.py` use debug options and registers that the core
  does not have. The `--with-ptp` option of the LiteX-Boards targets fails for the same reason.

## In the code {#ptp-code}

| File | Main classes |
|------------------------------------------------------|----------------------------------------------|
| `core/ptp/__init__.py` | `LiteEthPTP` |
| `core/ptp/clock.py` | `LiteEthTSU`, `LiteEthPTPRxTimestamp` |
| `core/ptp/packet.py` | `LiteEthPTPTX`, `LiteEthPTPRX` |
| `core/ptp/control.py` | `LiteEthPTPControl` |
| `core/ptp/servo.py` | `LiteEthPTPClockServo` |
| `bench/arty_ptp.py`, `bench/acorn_baseboard_mini_ptp.py` | PTP bench targets |
| `test/test_ptp.py`, `test/test_ptp_servo.py` | Simulation tests |

Where to go next: [ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack) for the UDP ports and IGMP,
[Simulation and verification](#simulation) for the PTP tests, [Debugging](#debugging) for
measurements on hardware.
