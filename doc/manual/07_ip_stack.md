# ARP, IP, ICMP, UDP, DHCP and IGMP {#ip-stack}

This chapter describes the protocol layers of the hardware stack: how ARP resolves addresses, what
IPv4 checks and sets, how ICMP answers ping, how UDP dispatches packets to user ports, and the DHCP
client and IGMP joiner.

Read it to know what the core accepts and drops, or to size a UDP port. The protocols are explained
in [ARP, IP, UDP and Etherbone](#network-primer).

## At a glance {#stack-glance}

| Item | Value |
|-------------------|------------------------------------------------------------------------|
| ARP | Replies to requests for its IP; cache of at least 2 entries, cleared after 1 s idle |
| ARP requests | One every 100 ms while unresolved, failure after 7 requests |
| IPv4 TX | No options, no fragmentation, TTL 128, header checksum |
| IPv4 RX | No options, no fragments, checksum verified, nonempty payload |
| Destinations | Unicast through ARP and an optional gateway, broadcast, multicast |
| ICMP | Echo reply, payload up to 128 bytes by default |
| UDP | Dispatch by destination port, zero checksum on TX, no checksum check on RX |
| DHCP | Client for one lease, user logic triggers it |
| IGMP | IGMPv2 Membership Reports for a fixed list of groups |
| Not supported | TCP, IPv6, IP options, fragment reassembly, ICMP errors |

## Layers {#stack-layers}

Each protocol is a pair of transmit and receive modules on the crossbar of the layer below; see
[Crossbars](#arch-crossbars). The figure below shows the IP and ARP layers and how they meet in the
ARP table.

![Protocol layers. Follow the cyan paths: UDP packets go down through IP TX to the MAC crossbar
and come up through IP RX. Look at the ARP table in the middle: IP TX asks it for the MAC address,
ARP RX feeds it.](stack_layers.svg)

## ARP {#stack-arp}

ARP maps the IP address of a host on the local network to its MAC address. The ARP receiver
accepts packets for Ethernet and IPv4 with the expected address sizes and whose target IP address
is the core's own address. A request makes the core reply; a reply updates the table.

### Table and cache {#stack-arp-table}

The ARP table answers the lookups of IP TX, sends the replies and requests, and keeps a small cache
of resolved addresses. The figure below shows its state machine.

![ARP table state machine. Follow a lookup: IDLE, CHECK_TABLE, then either a hit straight to
PRESENT_RESPONSE, or a miss through SEND_REQUEST. Requests repeat every 100 ms until a reply or the
seventh request.](stack_arp.svg)

A lookup searches the cache one entry per cycle. On a hit, the table answers at once. On a miss, it
sends an ARP request and remembers the IP address it is waiting for.

While that request is pending, a timer resends it every 100 ms; after 7 requests without a reply,
the table answers with a failure. A reply updates the cache only when it is for the pending address.

The cache has `arp_entries` entries, at least 2 (the core default of 1 gives 2). New entries
replace old ones in turn. A timer clears the whole cache after one second without any lookup or
update; `clear_enable` stops it. Every lookup restarts the timer, so with steady traffic the
entries never expire, even if a host changes its MAC address.

While the table is busy, an incoming ARP packet is lost; the peer's retries recover it.

| Parameter | Value | Where |
|----------------------|-------------------------------|-----------------------------------|
| Entries | `arp_entries`, at least 2 | `LiteEthIPCore`, `LiteEthUDPIPCore` |
| Cache clear | After 1 s without lookup, while `clear_enable` | `LiteEthARPCache` |
| Request interval | 100 ms | `LiteEthARPTable` |
| Requests before failure | 7 (the `max_requests` argument, 8, counts the failure check as one) | `LiteEthARPTable` |

## IPv4 transmit {#stack-ip-tx}

IP TX adds the IPv4 header to the packets of its users and finds the MAC address of the next hop.
The figure below shows its parts.

![IP TX. Follow the cyan path: the buffer, the packetizer that inserts the header with its
checksum, and the gate that sends or drops the packet once the destination selection has the MAC
address.](stack_ip_tx.svg)

The header is fixed except for the addresses, the length and the protocol:

| Field | Value |
|----------------------|----------------------------------------------------|
| Version, IHL | 4, 5 (20-byte header, no options) |
| Identification | 0 |
| Flags and offset | 0 (no fragmentation, DF not set) |
| TTL | 128 |
| Protocol | From the user port: 1 (ICMP), 2 (IGMP), 17 (UDP) |
| Source address | The core's IP address |
| Header checksum | Computed per packet |

The destination selection looks at the destination IP address of each packet:

- **Broadcast.** An address whose last byte is 255 goes to `ff:ff:ff:ff:ff:ff`, whatever the
  netmask. This test comes before the gateway: such an address is never resolved or sent to the
  gateway, even on a network wider than /24.
- **Multicast.** An address in 224.0.0.0/4 goes to `01:00:5e` followed by the low 23 bits of the
  address.
- **Unicast.** Any other address goes through an ARP lookup. With `gateway_ip` and `netmask` (give
  both or neither), an address outside the local network is resolved as the gateway instead.

When the lookup fails, the packet is dropped and `target_unreachable` pulses. A dropped packet
still completes its checksum, so the next packet starts clean. Each unicast packet makes its own
lookup; on a hit the cost is a few cycles.

### Header checksum {#stack-checksum}

The checksum is the ones' complement sum of the header. Up to 64 bits, it is computed serially
from the header fields, one 16-bit word per cycle, while the packetizer waits. Above 64 bits, a
registered adder tree computes it in a fixed short time (`with_parallel`), and the destination
lookup runs at the same time.

## IPv4 receive {#stack-ip-rx}

IP RX removes the IPv4 header and keeps a packet only when all these checks pass:

- version 4 and IHL 5, so no options;
- a valid header checksum;
- no fragment: neither a fragment offset nor the More Fragments flag;
- a total length larger than the 20-byte header, so a nonempty payload;
- the destination address, when `with_ip_broadcast` is `False`.

A rejected packet is drained to its end before the next one. The user port `length` is the declared
payload length. IP RX forwards the payload bytes as received, Ethernet padding included; UDP and
ICMP trim the padding with their own lengths.

### Destination policy {#stack-ip-broadcast}

The `with_ip_broadcast` option, `True` by default, controls the only destination check of IP RX.
Its name is misleading: `True` does not filter for broadcast, it removes the destination check.

| `with_ip_broadcast` | Packets accepted |
|----------------------------|--------------------------------------------------------------------|
| `True` (default) | Any destination: own address, broadcast, multicast, other hosts |
| `False` | Only the core's own address |

`True` is what DHCP and multicast need: a DHCP reply goes to the broadcast address, since the client
sets the broadcast flag, and a PTP message to a group address. Designs that need a strict filter
select `False` and lose these. The MAC adds no address filter in `crossbar` mode; see [MAC
limitations](#mac-limits).

## ICMP {#stack-icmp}

ICMP answers ping. The receiver keeps echo requests with code 0 and drops every other ICMP message.
The echo block stores the whole request before replying, then sends it back as an echo reply with
the same identifier, sequence number and payload.

The echo block holds one request at a time, of up to `icmp_fifo_depth` payload bytes (128 by
default). It discards a request that is longer, has an error, or whose received bytes do not match
its declared length.

The reply checksum is the request checksum adjusted for the new type; the request checksum itself is
not verified. With the default depth, `ping -s 128` gets an answer and larger payloads do not.

## UDP {#stack-udp}

UDP TX adds the 8-byte header with the ports and the length given on the user port. The checksum
field is 0, which IPv4 allows: the core relies on the Ethernet FCS. The IP address and length are
held until the last beat, even on wide streams where the header shifts the payload by one beat.

UDP RX keeps packets of protocol 17 whose UDP length is above 8 and fits the IP payload. It trims
the Ethernet padding using the UDP length, so the user sees exactly the payload.

A packet that ends before its declared length gets `error` on its last beat; a packet truncated
inside its header produces no output. The UDP checksum of received packets is not checked.

The UDP crossbar dispatches each packet by destination port; a packet for a port that nobody
owns is drained silently, without an ICMP port unreachable message.

### UDP user ports {#stack-udp-ports}

User logic gets a UDP port from the crossbar with `get_port()`. The port carries the fields of the
table below, on a sink for transmit and a source for receive.

| Field | Width | Meaning |
|----------------|------------|--------------------------------------------------------|
| `src_port` | 16 | Local port (TX), remote port (RX) |
| `dst_port` | 16 | Remote port (TX), local port (RX) |
| `ip_address` | 32 | Remote IP address: destination (TX), source (RX) |
| `length` | 16 | Payload length in bytes |
| `data` | `dw` | Payload, first byte on the low bits |
| `be` | `dw`/8 | Valid bytes; see [Byte enables](#arch-be) |
| `error` | `dw`/8 | Error per byte lane (RX) |

`get_port(udp_port, dw, cd, depth, tx_buffer_depth)` builds the chain of the figure below. The
port lives in the clock domain `cd` at width `dw`, and the crossbar converts both.

![A UDP user port. Follow the TX row: clock crossing into the core, width conversion, optional
packet FIFO. The RX row mirrors it, with a drop FIFO instead.](stack_udp_ports.svg)

The packet FIFOs matter when the port is slower than the core: narrower, or in another clock
domain. A narrow port fills a wide crossbar word only every few cycles, and a PHY that cannot pause
a frame cannot wait for it. Two options add whole-packet buffers:

- **Automatic.** When the core uses store-and-forward and the port is slower, the port gets a TX
  packet FIFO and an RX drop FIFO of one full frame each.
- **Explicit.** `tx_buffer_depth`, in crossbar words, adds a TX packet FIFO of that depth. It must
  hold the largest packet sent on the port; a longer packet blocks the port.

The `depth` argument sets the depth of the clock crossings; the default comes from LiteX.

## DHCP {#stack-dhcp}

The DHCP client gets an IP address from a DHCP server (`LiteEthDHCP`). It runs on a 32-bit UDP
port that the user takes on port 68, and is driven by user logic through a few signals:

| Signal | Direction | Meaning |
|----------------|-----------|--------------------------------------------------|
| `start` | in | Start a DISCOVER, OFFER, REQUEST, ACK exchange |
| `mac_address` | in | Client MAC address, sent in the request |
| `done` | out | No exchange in progress |
| `timeout` | out | The exchange took longer than `timeout` (1 s by default) |
| `ip_address` | out | Address offered by the server, set when the ACK arrives |
| `lease_time` | out | Lease time of the last ACK, in seconds |

The client broadcasts its DISCOVER and REQUEST with the same transaction identifier, a new one for
each `start`, and accepts only replies with it. From the server's messages it keeps the offered address,
the server identifier and the lease time. A timeout resets the client to idle, ready for a new
`start`.

The client does not reconfigure the core. User logic feeds `ip_address` to the core's IP address
input, which is a signal when the address is not fixed at build time. It also renews the lease:
the client issues no request on its own before the lease expires. The subnet mask and router are
requested from the server but not parsed.

## IGMP {#stack-igmp}

Some switches forward multicast traffic only to ports that have joined the group. The IGMP joiner
(`LiteEthIGMPJoiner`) sends an IGMPv2 Membership Report for each group of a fixed list, every
`interval` seconds (10 by default), from an IP port of protocol 2. Each report goes to its group
address, so IP TX maps it to the group's multicast MAC address.

The core adds the joiner with `with_igmp` and `igmp_groups`; LiteX passes them from
`add_etherbone()`. The joiner waits for its `enable` input, the PHY's `link_up` by default, and
sends the first reports one interval after it. It discards received IGMP packets, so it does not
answer queries or send leave messages.

## Wide datapaths {#stack-wide}

At 128, 256 or 512 bits, the protocol layers process wide beats while the MAC framing stays at
64 bits; see [Data widths](#arch-widths). The width changes how headers are processed:

- the IPv4 header checksum uses the parallel adder tree;
- IP TX resolves the destination while it computes the checksum;
- IP RX and UDP RX validate the header and forward the first payload beat in the same cycle;
- UDP and ICMP keep the IP address and length of a packet until its last beat leaves, so a payload
  held back by backpressure never takes the metadata of the next packet.

The repository records the header-path cost with `bench/udp_throughput.py`: the steady interval
between packets, in core clock cycles, through UDP, IP and MAC header processing with a resolved
ARP entry, without the PHY or the wire. The table below quotes `doc/high_speed_datapaths.md`. It
was recorded before the packet-start overlap of the list above, which the code has, so its
parallel column is a few cycles above what the code gives.

| Width | Payload (bytes) | Serial checksum | Parallel checksum |
|-------|-----------------|-----------------|-------------------|
| 128 | 18 | 17 | 8 |
| 256 | 18 | 14 | 7 |
| 512 | 18 | 14 | 6 |
| 128 | 1472 | 119 | 103 |
| 256 | 1472 | 72 | 56 |
| 512 | 1472 | 49 | 33 |
| 128 | 8972 | 588 | 572 |
| 256 | 8972 | 307 | 291 |
| 512 | 8972 | 166 | 150 |

With the overlap, the same page records 6, 5 and 4 cycles for 18-byte payloads at 128, 256 and
512 bits. The command below measures the code as it is:

```sh
PYTHONPATH=. python3 bench/udp_throughput.py --data-width 128 256 512 --length 18 1472 8972 --packets 4
```

Short packets keep a fixed per-packet cost, so a
wider core alone does not reach line rate with minimum-size frames at 100G. Timing results are in
[Performance](#performance).

## Limitations {#stack-limits}

- No TCP, no IPv6, no IP options, no fragmentation or fragment reassembly.
- No UDP checksum on transmit or receive; integrity relies on the Ethernet FCS.
- No ICMP errors (port or host unreachable) and no ICMP message other than the echo reply.
- ARP entries never expire under steady traffic, the whole cache is cleared after one idle
  second, and incoming ARP packets are lost while the table is busy.
- Destinations with a last byte of 255 are always sent as broadcast.
- IGMP reports carry the IP TTL of 128 used for every packet and no Router Alert option.
- The DHCP client does not renew its lease and does not parse the subnet mask or router.

## In the code {#stack-code}

| File | Main classes | Role |
|------------------------|------------------------------------------------------|------------------------------|
| `core/arp.py` | `LiteEthARP`, `LiteEthARPTable`, `LiteEthARPCache` | Address resolution |
| `core/ip.py` | `LiteEthIP`, `LiteEthIPTX`, `LiteEthIPRX` | IPv4, checksum, IP crossbar |
| `core/icmp.py` | `LiteEthICMP`, `LiteEthICMPEcho` | Ping replies |
| `core/udp.py` | `LiteEthUDP`, `LiteEthUDPCrossbar` | UDP, user ports |
| `core/dhcp.py` | `LiteEthDHCP` | DHCP client |
| `core/igmp.py` | `LiteEthIGMPJoiner` | IGMPv2 reports |

Where to go next: [Etherbone, UDP streamer and raw ports](#frontends) for ready-made users of the
UDP ports, [Precision Time Protocol](#ptp) for a multicast user, [Integration](#integration) to
set the addresses.
