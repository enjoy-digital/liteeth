# Etherbone, UDP streamer and raw ports {#frontends}

This chapter describes the frontends: the ready-made blocks that connect user logic or a SoC bus to
the UDP ports of the core. Etherbone gives a host access to a Wishbone bus, the UDP streamer turns a
data stream into UDP packets and back, and raw ports expose a UDP port as it is.

Read it to pick a frontend and to use it from the host. The Etherbone protocol is introduced in
[ARP, IP, UDP and Etherbone](#network-primer).

## At a glance {#fe-glance}

| Frontend | Class | Interface to user logic | Host side |
|--------------------|---------------------------------------|-----------------------|------------------------------|
| Etherbone master | `LiteEthEtherbone` | Wishbone master on the SoC bus | `litex_server`, `RemoteClient` |
| Etherbone slave | `LiteEthEtherbone` | Wishbone slave | An Etherbone peer |
| UDP streamer | `LiteEthUDPStreamer` | Data stream in each direction | Any UDP socket |
| Raw UDP port | `get_port()` | Full UDP user port | Any UDP socket |

## Choosing a frontend {#fe-choose}

The frontends trade generality for simplicity. The table below lists the usual choice for each
need.

| Need | Frontend |
|-------------------------------------------------|---------------------------------------|
| Read and write registers and memories from a PC | Etherbone master |
| Debug a SoC with LiteX host tools or LiteScope | Etherbone master |
| Stream samples or data to a fixed host | UDP streamer |
| Several peers, per-packet addresses and ports | Raw UDP port |
| Ports chosen at run time in a standalone core | Raw port or streamer with dynamic pins |

An FPGA hard MAC is not a UDP frontend: it takes the place of a PHY through the
[AXI-Stream adapter for hard MACs](#mac-axis).

## Etherbone {#fe-etherbone}

Etherbone carries Wishbone accesses in UDP packets. The format is the Etherbone protocol; LiteEth
implements the subset that a host needs to drive a SoC: 32-bit addresses and data, one record per
packet, no address spaces. The figure below shows the master, the usual mode, in which the host
reaches the SoC bus.

![Etherbone master. Follow a record from the UDP port: the packet check, the record receiver that
validates it, the Wishbone master that runs the accesses, and the reply built from the read data.
Probe packets get their reply directly.](fe_etherbone.svg)

### Packets {#fe-eb-packets}

Each packet starts with an 8-byte Etherbone header. The packet check keeps packets with the
Etherbone magic `0x4e6f`, version 1, 4-byte addresses and data, and a payload after the header; it
drops the others.

A packet with the probe flag gets the same packet back with the probe reply flag: this is how
`litex_server` discovers a device. Other packets carry one record.

### Records {#fe-eb-records}

A record holds a header, then the writes (a base address and the data words), then the reads (a
return address and one address per word). The record receiver stores the whole record and checks
it before any bus access:

- the write and read counts match the words received;
- the record, its base and return addresses included, fits in `buffer_depth` + 1 words
  (`buffer_depth` writes or reads alone, one fewer when it has both);
- every word has a full byte mask and no error;
- no unsupported flag is set: no address space, FIFO mode or cycle flag.

A record that fails a check is dropped whole. A valid record runs its writes first, at incrementing
word addresses from the base address, with the record's byte enables on the Wishbone `sel` lines.

Then it runs its reads, one access per address. A record with reads gets a reply to the address and
port it came from: a record of writes holding the read data, addressed to the return address. A
record with only writes gets no reply.

The block handles one record at a time: the next record waits until the reply of the previous one
has left. The Wishbone master issues one access at a time and waits for its acknowledge; it has no timeout
of its own and ignores `err`. In a LiteX SoC the bus timeout (10^6 cycles by default) ends a hung
access, and a read then returns undefined data. Data words are byte-swapped to
big-endian order on the wire; this order is fixed.

| Parameter | Default | Meaning |
|----------------------|------------------------------------|----------------------------------------|
| `udp_port` | (required); 1234 in LiteX | Local UDP port |
| `mode` | `"master"` | `master` (host to SoC) or `slave` (SoC to peer) |
| `buffer_depth` | 4; 16 in LiteX and the generator | Largest record, in operations (2 to 256) |
| `cd` | `"sys"` | Clock domain of the Wishbone side |

`test_etherbone.py --speed` sends bursts of 255 words, and most bench targets build Etherbone
with a `buffer_depth` of 255 or 256 to take them in one record. With a smaller `buffer_depth`, a
longer record is dropped whole: its writes are lost silently and its reads time out on the host.

On a UDP crossbar wider than 32 bits, the block adds a transmit buffer that holds its largest
reply, so that replies leave back-to-back.

### Slave mode {#fe-eb-slave}

In `slave` mode the roles are reversed: Etherbone exposes a Wishbone slave, and each access on it
becomes a one-word record sent to a remote Etherbone device. A read waits for the reply.

The records go to the address and port of the last packet received on the Etherbone port, so the
peer must contact the FPGA first. Before the peer has sent a packet, records go to 0.0.0.0 and are
dropped, and a read on the slave bus never completes. No test or bench target uses this mode.

### Throughput {#fe-eb-throughput}

Etherbone runs one record at a time, so its throughput depends on the burst length that the host
uses. Reads wait for each reply and show the round-trip limit of the link; writes get no reply, so
a host-side write rate measures how fast the host sends them. [Etherbone](#perf-etherbone) gives a
public burst measurement and how to read it. For bulk data, a UDP streamer or a raw port is the
better choice.

### Host side {#fe-eb-host}

On the host, `litex_server` opens the Etherbone link and serves it to the LiteX host tools, which
then use `RemoteClient` with the `csr.csv` of the build:

```sh
# Bridge the board on 192.168.1.50 to the LiteX host tools.
litex_server --udp --udp-ip=192.168.1.50 --udp-port=1234
# Then, in another terminal, read the identifier and test a memory.
python3 bench/test_etherbone.py --ident --sram --speed
```

`test_etherbone.py` reads the SoC identifier (`--ident`), writes and reads one word (`--access`),
fills and dumps the start of the `sram` region (`--sram`), and times about 16 KiB of writes and
reads in bursts of 255 words (`--speed`, in KiB/s). The last two need an `sram` region; writes get
no reply, so the write rate is the host's send rate. Look for the identifier string of your build:
it proves that ARP, IP, UDP and Etherbone all work.

`--udp-scan` probes every address of a /24 network and lists the devices that answer; [Step 5:
start litex_server](#gs-server) shows a capture.

A Wireshark dissector decodes Etherbone packets on UDP port 1234
(`liteeth/software/dissector/etherbone.lua`). Copy it to the Wireshark plugin folder, then look
for the record counts and addresses of each packet; [Debugging](#debugging) shows how to use it.

## UDP streamer {#fe-streamer}

The UDP streamer exchanges a plain data stream with one remote host on one UDP port. User logic
writes words to its `sink` and reads words from its `source`; the streamer adds and removes the
UDP packets. The figure below shows its two halves.

![UDP streamer. Follow the TX row: the packet FIFO cuts the stream into packets, the destination
latch adds the IP address and port. On the RX row, the filter keeps the packets for the
port.](fe_streamer.svg)

| Parameter | Default | Meaning |
|-----------------------------------|---------------|--------------------------------------------------|
| `ip_address` | (required) | Remote IP address, a constant or a signal |
| `udp_port` | (required) | UDP port, local and remote, a constant or a signal |
| `data_width` | 8 | Width of `sink` and `source` |
| `tx_fifo_depth` | 64 | TX packet FIFO in words, `None` for none |
| `rx_fifo_depth` | 64 | RX FIFO in words, `None` for none |
| `tx_max_packet_length` | `None` | Largest TX packet in bytes, else the FIFO depth |
| `with_broadcast` | `True` | `False`: accept only packets from `ip_address` |
| `with_be` | `False` | Add `be` to `sink` and `source` |
| `cd` | `"sys"` | Clock domain of `sink` and `source` |

### Transmit {#fe-streamer-tx}

With a TX FIFO, the streamer closes a packet on `last`, or when it reaches the largest packet
length. That length is `tx_max_packet_length` rounded down to whole words and capped at
`tx_fifo_depth`, or the FIFO depth when it is not set.

Set `tx_max_packet_length` to the largest UDP payload the network carries, 1472 bytes for the usual
1500-byte IP MTU or 8972 bytes for 9000-byte jumbo frames, and make `tx_fifo_depth` hold that many
words, so that split packets fit in frames. Each packet leaves only
when it is complete in the FIFO.

Without a TX FIFO, each word is a packet of its own, which suits slow control values. Packets go to
`ip_address` with `udp_port` as both source and destination port.

The streamer samples the destination when a packet's first beat is presented and holds it until its
last beat, even under backpressure. Clearing `enable` stops the next packet from starting, never one
already presented.

With `with_be`, `be` marks the valid bytes of each word, following the
[Byte enables](#arch-be) rules: full intermediate words, a contiguous mask on the last. Without it,
every word is full and the packet length is a multiple of the width.

### Receive {#fe-streamer-rx}

The streamer keeps the received packets whose destination port is `udp_port`. With
`with_broadcast=False`, it also requires the source address to be `ip_address`.

It decides on the first beat of each packet and holds the decision to the end, even if the settings
change in the middle. The `source` stream carries the payload words, `last`, a one-bit `error` (any
lane in error) and, with `with_be`, the byte mask.

The RX FIFO is a word FIFO, not a packet store: when it is full, it backpressures the core. User
logic must keep up with the received traffic, since the PHY cannot pause a frame; see
[Buffering and drops](#arch-buffering).

### Settings {#fe-streamer-settings}

The two halves of the streamer, `LiteEthStream2UDPTX` and `LiteEthUDP2StreamRX`, have `enable`,
`ip_address` and `udp_port` signals. Built separately with `with_csr`, each half exposes them as
registers of the same names; `LiteEthUDPStreamer` does not pass this option. The standalone
generator wires the address and port to pins when a streamer port has no fixed `ip_address`; see
[Integration](#integration).

The Arty bench target shows a small use: a streamer on port 6000 drives the LEDs from received
bytes, and `bench/test_udp_streamer.py --leds` exercises it from the host. The part that would send
the switch values is disabled in the target, so `--switches` receives nothing.

## Raw UDP ports {#fe-raw}

A raw port is a UDP user port with nothing on top: user logic drives and reads the fields of
[UDP user ports](#stack-udp-ports) directly. It sets the remote address, both ports and the length
of every packet it sends, and sees them for every packet it receives. Inside a LiteX design, any
call to `get_port()` gives one.

The standalone generator exposes a raw port as pins (`mode: raw` in a `udp_ports` entry): `sink_*`
and `source_*` signals for the valid, ready, last, data, `be`, IP address, both ports and length,
plus `source_error`. Its receive side keeps the packets whose destination port equals the value on
`sink_dst_port`: the crossbar uses that pin as the port of the raw port. The same pin sets the
destination port of transmitted packets, so a raw port listens on the port it sends to.

## AXI-Stream interfaces {#fe-axis}

The frontends speak LiteX streams, which map to AXI-Stream with a few rules:

| AXI-Stream | LiteX stream | Note |
|--------------|--------------|-----------------------------------------------------------|
| `tvalid` | `valid` | |
| `tready` | `ready` | |
| `tlast` | `last` | One packet per `tlast` |
| `tdata` | `data` | First byte on the low bits |
| `tkeep` | `be` | Contiguous from byte 0, full except on the last beat |
| `tuser` | `error` | Per lane in the core; one bit on the streamer `source` |

The generator's streamer ports follow this table: `sink_keep` and `source_keep` are the byte
masks, and `with_tkeep: False` removes them for whole-word streams. For a hard MAC with an
AXI-Stream interface, the adapter of [AXI-Stream adapter for hard MACs](#mac-axis) takes the place
of a PHY.

## Limitations {#fe-limits}

- Etherbone: one record per packet, 32-bit addresses and data, no address spaces, no FIFO
  addressing, no Wishbone timeout, Wishbone `err` ignored; the data byte order is not
  configurable.
- Etherbone probes: the probe path buffers one payload word, so a probe packet with two or more
  payload words stalls the Etherbone receive path. `litex_server` sends one-word probes.
- Etherbone slave mode sends to the last peer seen, to 0.0.0.0 before any packet (a slave read
  then never completes), and is not covered by the tests.
- UDP streamer: one remote host and one port per streamer; the RX FIFO does not drop whole packets
  when user logic falls behind.
- Raw ports in the generator receive on the port given by their `sink_dst_port` pin, which is
  also the destination port of the packets they send.
- The Arty bench target does not send its switch values (`--switches` of
  `test_udp_streamer.py` receives nothing).

## In the code {#fe-code}

| File | Main classes | Role |
|-----------------------------------|-------------------------------|----------------------------|
| `frontend/etherbone.py` | `LiteEthEtherbone` | Etherbone master and slave |
| `frontend/stream.py` | `LiteEthUDPStreamer` | UDP streamer and its two halves |
| `gen.py` | `UDPCore` | Generator streamer and raw ports |
| `test_etherbone.py` | | Host test of Etherbone (`bench/`) |
| `test_udp_streamer.py` | | Host test of the Arty streamer (`bench/`) |

The Wireshark dissector is `liteeth/software/dissector/etherbone.lua`.

Where to go next: [Precision Time Protocol](#ptp) for another UDP user, [Getting
started](#getting-started) for a first Etherbone link, [Integration](#integration) for
`add_etherbone()` and the generator.
