# ARP, IP, UDP and Etherbone {#network-primer}

This chapter explains the protocols that ride inside the Ethernet frame: ARP to find a MAC address,
IPv4 to address a host, ICMP to answer ping, UDP to carry data to a port.

It then covers the protocols that LiteEth builds on UDP: DHCP, IGMP, Etherbone and PTP, and ends
with TFTP, which the LiteX BIOS uses to boot over the network.

Each section starts with the idea, then gives the mechanism, and ends with the part of LiteEth that
implements it; [ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack), [Etherbone, UDP streamer and raw
ports](#frontends) and [Precision Time Protocol](#ptp) describe the implementation. The protocols
are cited by their RFC.

## Why a hardware stack {#net-why}

On a PC, the operating system runs the network stack in software. An FPGA design often has no CPU,
or a small one that cannot keep up with a gigabit link. It usually needs only a few things from the
network: answer ARP and ping, receive and send UDP datagrams, and maybe expose its registers.

LiteEth does this in gateware. Each layer is a small pipeline that adds or removes a header as the
data streams through, so the latency is a few clock cycles per layer and does not depend on any
software. The price is a narrow feature set: no TCP, no IPv6, no IP options or fragments.

## Headers in a frame {#net-headers}

Each layer adds a header in front of the data of the layer above. A UDP datagram sent by LiteEth
therefore travels in an IPv4 packet, itself in an Ethernet frame. The figure below shows the IPv4
and UDP headers, 32 bits per row, as they follow the 14-byte Ethernet header.

![The IPv4 and UDP headers, 32 bits per row. The IPv4 header is 20 bytes long when it carries no
options; the UDP header is 8 bytes long. The payload follows the UDP header.](net_headers.svg)

All multi-byte fields of these headers are sent most significant byte first, the *network byte
order*. With the 14 bytes of the Ethernet header, a UDP payload starts 42 bytes into the frame.

## ARP {#net-arp}

IP addresses name hosts, but Ethernet delivers frames to MAC addresses. Before sending to an IP
address on its network, a host must find the matching MAC address. *ARP*, the address resolution
protocol (RFC 826), asks for it.

The sender broadcasts an ARP *request*: "who has 192.168.1.50? Tell 192.168.1.100". The host that
owns the address answers with a unicast ARP *reply* carrying its MAC address. The sender keeps the
answer in a *cache* for later frames. The figure below shows the exchange.

![An ARP exchange. The host broadcasts a request for the IP address of the board; the board answers
with its MAC address; the host can then send IP packets to it.](net_arp.svg)

| Field | Size | Content in a request |
|-------------------|---------|----------------------------------------------------|
| Hardware type | 2 bytes | 1, Ethernet |
| Protocol type | 2 bytes | `0x0800`, IPv4 |
| Sizes | 2 bytes | 6 and 4: MAC and IP address sizes |
| Opcode | 2 bytes | 1 for a request, 2 for a reply |
| Sender MAC and IP | 10 bytes | Address of the sender |
| Target MAC and IP | 10 bytes | Zero MAC, IP address being resolved |

An ARP packet is 28 bytes long and travels directly in an Ethernet frame, with EtherType `0x0806`.
It is padded to the minimum frame size like any short payload.

In LiteEth, the ARP layer answers requests for its own IP address and resolves the destinations of
outgoing packets. Its cache is small, two entries at least, and is cleared after one second without
lookup. An unresolved address is requested every 100 ms; after 8 requests without a reply the packet
is dropped ([ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack)).

## IPv4 {#net-ipv4}

*IPv4* (RFC 791) addresses hosts with 32-bit addresses, written as four bytes, such as 192.168.1.50.
Its header carries the source and destination addresses, the protocol of the payload and the length
of the packet. The table below lists the fields and what LiteEth puts in them.

| Field | Size | Content | LiteEth sends |
|------------------|---------|------------------------------------------------|------------------|
| Version, IHL | 1 byte | 4, and header length in 32-bit words | 4, 5 |
| DSCP, ECN | 1 byte | Priority and congestion marking | 0 |
| Total length | 2 bytes | Header and payload, in bytes | Computed |
| Identification | 2 bytes | Groups the fragments of one packet | 0 |
| Flags, offset | 2 bytes | Don't fragment, more fragments, fragment offset | 0 |
| TTL | 1 byte | Time to live: hops left before the packet dies | 128 |
| Protocol | 1 byte | 1 for ICMP, 2 for IGMP, 17 for UDP | Per packet |
| Checksum | 2 bytes | Checksum of the header | Computed |
| Source, destination | 8 bytes | IP addresses | Per packet |

The *header checksum* is the one's complement of the one's complement sum of the header, taken as
16-bit words. A receiver adds up the whole header, checksum included, and accepts it when the result
is zero. It protects the header only; the payload relies on its own checks.

A router may split a packet too large for the next link into *fragments*, which the destination
reassembles. Fragments are rare on a local network and costly to reassemble in gateware.

A *netmask*, such as 255.255.255.0, says which addresses are on the local network. A packet to a
local address goes straight to the MAC address of its destination; a packet to another network goes
to the MAC address of the *gateway*, the router of the local network.

| Address | Meaning |
|---------------------------|--------------------------------------------------------------|
| 255.255.255.255 | Broadcast to the local network; sent to MAC `ff:ff:ff:ff:ff:ff` |
| 224.0.0.0 to 239.255.255.255 | Multicast groups; sent to MAC `01:00:5e` followed by the low 23 bits of the group |
| 192.168.1.50 | Default IP address of a LiteX SoC with Etherbone |

In LiteEth, the IP layer sends packets with the fields of the table and never fragments them. It
drops received packets with options, fragments, an empty payload or a bad header checksum. By
default it accepts any destination address (`with_ip_broadcast`). It sends any address ending in 255
to the broadcast MAC address, whatever the netmask. A netmask and a gateway are optional parameters;
without them, every destination is resolved directly ([ARP, IP, ICMP, UDP, DHCP and
IGMP](#ip-stack)).

## ICMP and ping {#net-icmp}

*ICMP* (RFC 792) carries control messages for IP. The best known pair is the *echo request* (type 8)
and the *echo reply* (type 0): `ping` sends requests, and the destination answers each one with a
reply carrying the same identifier, sequence number and payload.

In LiteEth, the ICMP layer answers echo requests and sends nothing else. It stores the whole request
before replying, so a request with a payload larger than its buffer, 128 bytes by default, gets no
reply. The default `ping` of a Linux host sends 56 bytes and fits.

## UDP {#net-udp}

*UDP* (RFC 768) adds *ports* to IP: a 16-bit number that selects an application on the host. Its
header holds the source port, the destination port, the length of the datagram and a checksum. A
datagram is sent once: UDP has no acknowledgement, no retry and no flow control.

The UDP checksum covers the header, the payload and a pseudo header of IP addresses. Over IPv4 it is
optional: a sender that writes zero tells the receiver not to check it. A corrupted frame is then
caught by the Ethernet CRC only; a lost datagram is not detected at all.

In LiteEth, the UDP layer sends a zero checksum and does not check the checksum of received
datagrams. It dispatches received datagrams to user ports by destination port number, each with its
own clock domain and data width ([ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack)).

## DHCP {#net-dhcp}

*DHCP* (RFC 2131) gives a host its IP address from a server on the network. The client broadcasts a
DISCOVER from UDP port 68 to port 67; a server answers with an OFFER; the client REQUESTs the
offered address, and the server confirms with an ACK. The figure below shows the exchange.

![A DHCP exchange. The board broadcasts a DISCOVER; the server offers an address; the board requests
it and the server acknowledges it with a lease time.](net_dhcp.svg)

The address is a *lease*: it is valid for a time given in the ACK, and the client must renew it
before the lease expires. DHCP messages carry their parameters as *options*, such as the message
type (option 53), the requested address (50), the server identifier (54) and the lease time (51).

In LiteEth, `LiteEthDHCP` is a hardware DHCP client: it runs the four-message exchange on a UDP port
and outputs the IP address and the lease time. Renewal is left to user logic, which must start a new
request before the lease expires. The LiteX BIOS also has its own DHCP client, in software ([ARP,
IP, ICMP, UDP, DHCP and IGMP](#ip-stack)).

## IGMP and multicast {#net-igmp}

A *multicast* group is an IP address from 224.0.0.0 to 239.255.255.255 that several hosts can listen
to. A switch would have to flood multicast frames to all its ports, unless it knows who listens.
With *IGMP snooping*, it watches the IGMP messages of the hosts and forwards each group to the ports
that joined it.

A host joins a group by sending a *membership report* for it (IGMPv2, RFC 2236). A router or a
switch sends *queries* from time to time, and hosts answer with reports for the groups they still
want. A host that stays silent drops out of the group after a timeout.

In LiteEth, `LiteEthIGMPJoiner` sends IGMPv2 membership reports for a list of groups every 10
seconds by default, and ignores queries: the periodic reports keep the groups alive.

The reports leave with the TTL of the IP layer, 128, and without the Router Alert option, while RFC
2236 specifies a TTL of 1 and that option; switches that snoop IGMP usually accept them. The PTP
targets enable the joiner for the PTP multicast groups ([ARP, IP, ICMP, UDP, DHCP and
IGMP](#ip-stack)).

## Etherbone {#net-etherbone}

*Etherbone* carries Wishbone bus accesses in UDP datagrams. A host sends writes and reads to a
device on the network; the device performs them on its bus and returns the read data. The protocol
comes from the White Rabbit timing project, where it carries register accesses over the network.

An Etherbone packet starts with an 8-byte header: the magic number `0x4e6f`, a version, flags and
the address and data sizes. One or more *records* follow. Each record has a 4-byte header with a
byte enable and two counts, then the writes, then the reads. The figure below shows a packet with
one record.

![An Etherbone packet with one record. The packet header carries the magic number and the sizes;
the record header gives the numbers of writes and reads; the writes are a base address and data
words, the reads a return address and the addresses to read.](net_etherbone.svg)

| Field | Content |
|------------------|------------------------------------------------------------------------|
| Magic | `0x4e6f` |
| Version | 1 |
| Flags | Probe flag (PF), probe response (PR), no reads (NR) |
| Sizes | Address and data sizes, a bit mask of 8 to 64 bits |
| Record header | Flags, byte enable, write count, read count |
| Writes | A base address, then one data word per write, at consecutive addresses |
| Reads | A base return address, then one address per read |

A host first sends a *probe*, a packet with the probe flag and no record; the device answers with
the probe response flag. To read, the host sends a record of reads with a base return address; the
device answers with a record of writes to that address, carrying the data it read.

In LiteEth, the Etherbone frontend implements a subset: 32-bit addresses and data, one record per
frame, no address spaces. It can be a Wishbone master, which lets a host drive the SoC bus, or a
slave, which lets the SoC reach a remote bus.

`add_etherbone` uses UDP port 1234 by default, and `litex_server --udp` bridges it to the LiteX
host tools ([Etherbone, UDP streamer and raw ports](#frontends)).

## PTP {#net-ptp}

*PTP*, the precision time protocol (IEEE 1588-2008), synchronizes clocks over a network. A *master*
sends its time; a *slave* measures the delay of the path and corrects its own clock. Unlike NTP, PTP
relies on timestamps taken in hardware, as close to the wire as possible.

In the *end-to-end* (E2E) mechanism, the master sends a Sync at time t1, which the slave receives at
t2. The slave sends a Delay_Req at t3, which the master receives at t4 and returns in a Delay_Resp.
The figure below shows the four timestamps.

![The end-to-end delay mechanism of PTP. The master sends Sync (t1) and Follow_Up carrying t1; the
slave timestamps the Sync (t2), sends a Delay_Req (t3), and receives t4 in the Delay_Resp. The four
timestamps give the offset and the delay.](net_ptp_e2e.svg)

With the four timestamps, the slave computes the path delay and its offset from the master:

- delay = ((t2 - t1) + (t4 - t3)) / 2
- offset = ((t2 - t1) - (t4 - t3)) / 2

The formulas assume a symmetric path: the same delay in both directions. A *two-step* master sends
t1 in a separate Follow_Up message; a *one-step* master writes it into the Sync itself. In the
*peer-to-peer* (P2P) mechanism, each link measures its own delay with Pdelay_Req and Pdelay_Resp
messages.

Over UDP/IPv4, the timestamped *event* messages (Sync, Delay_Req, Pdelay) use port 319 and the
*general* messages (Follow_Up, Delay_Resp, Announce) port 320. They go to the multicast group
224.0.1.129, or 224.0.0.107 for the peer-to-peer messages. Announce messages advertise the master.

A timestamp is only as good as the point where it is taken. Every stage between that point and the
wire adds its own latency and its jitter to the measurement, and the asymmetry between the transmit
and receive paths ends up in the offset.

In LiteEth, `LiteEthPTP` is a slave over UDP/IPv4 with the E2E and P2P mechanisms and one-step and
two-step masters. It takes its receive timestamps at the UDP user port, after the MAC and the stack,
not at the PHY. It takes its transmit timestamps when the PTP packetizer sends the message, before
UDP, IP and the MAC; the two paths are not symmetric. [Precision Time Protocol](#ptp) describes the
clock, the servo and this limit.

## TFTP and network boot {#net-tftp}

*TFTP*, the trivial file transfer protocol (RFC 1350), reads a file from a server over UDP. The
client asks for a file name on port 69; the server sends the file in blocks of 512 bytes, and the
client acknowledges each block. A block shorter than 512 bytes ends the transfer.

TFTP is not part of the LiteEth gateware. The LiteX BIOS runs it in software over the software MAC
to load a program, usually a Linux kernel and its files, into memory: this is *netboot*.

The BIOS first reads `boot.json`, which lists the files and their load addresses. The capture below
is a netboot as the BIOS prints it, from the LiteX wiki.

```text
Booting from network...
Local IP : 192.168.1.50
Remote IP: 192.168.1.100
Booting from boot.json...
Copying Image to 0x40000000... (4545524 bytes)
Copying rv32.dtb to 0x41000000... (2167 bytes)
Copying opensbi.bin to 0x40f00000... (53640 bytes)
Executing booted program at 0x40f00000
--============= Liftoff! ===============--
```

[Integration](#integration) describes the BIOS network commands and the netboot setup.

## Where to go next {#net-next}

Part II describes the implementation: [Core architecture](#architecture) first, then [ARP, IP, ICMP,
UDP, DHCP and IGMP](#ip-stack), [Etherbone, UDP streamer and raw ports](#frontends) and [Precision
Time Protocol](#ptp). [Getting started](#getting-started) pings a first board.
