# Packet contracts and pipeline ownership

LiteEth uses LiteX ready/valid streams. A transfer occurs on `valid & ready`.
While stalled, the producer holds data, mask, errors, `last` and parameters
stable. Parameters describe the entire packet and remain stable through its
final transfer. A block that buffers a tail after accepting input `last` must
retain those parameters until its output completes.

## Bytes, lengths and errors

Native packets contain at least one byte. `be` enables byte lanes from the
least significant byte: all lanes on intermediate beats, and a nonzero,
contiguous low mask on the final beat. Sparse masks need compaction before
entering LiteEth. See [byte-enabled streams](stream_byte_enables.md).

| Endpoint / field | Length in bytes |
| --- | --- |
| MAC / PHY stream | Determined by accepted `be` bits through `last` |
| IPv4 wire `total_length` | IPv4 header plus IPv4 payload |
| IPv4 user `length` | IPv4 payload, including the transport header |
| UDP wire `length` | Eight-byte UDP header plus application payload |
| UDP user `length` | Application payload |
| ICMP user `length` | Payload after the eight-byte ICMP header |
| Etherbone packet user `length` | Bytes after the eight-byte packet header |
| DHCP UDP `length` | Entire BOOTP/DHCP message |

A wire header does not make a zero-length native payload representable. UDP
and ICMP receive paths drop empty payloads. Their TX users must supply a
nonempty payload and a matching length. Neither an empty beat nor `be=0`
means a full word.

`error` has one bit per byte lane. Consumers qualify errors with `be`; the UDP
streamer's scalar error is the OR of the enabled lanes. A final error can
invalidate an entire packet, including bytes delivered earlier. Cut-through
consumers must wait for completion before committing irreversible operations.
A FIFO alone does not imply that errors are checked.

IPv4 RX currently forwards physical payload bytes, including Ethernet padding.
UDP and ICMP RX trim that padding using the declared transport/IP lengths. A
premature physical `last` retains the actual byte mask and marks the final
payload beat erroneous. UDP rejects lengths below its nonempty minimum or above
the enclosing IPv4 payload length. Truncation wholly inside a header produces
no output packet. Protocol checksum validation is separate: UDP TX uses the
IPv4 zero-checksum convention, and ICMP echo adjusts the received checksum
without independently validating it.

The MAC detects FCS errors at frame completion. In a cut-through path, an upper
layer that has already completed a shorter declared payload cannot retract it
when a later padding/FCS error arrives. Applications requiring whole-frame
integrity must buffer and validate at the appropriate boundary; enabling a
store-and-forward FIFO alone does not add an error-rejection policy.

## Receive consumers

- **ICMP echo** stores and validates the complete payload before replying. It
  rejects errors, inconsistent lengths and packets exceeding `fifo_depth`
  bytes. Reception drains through `last` on rejection, allowing the next packet
  to proceed. RX accepts echo requests with code zero.
- **Etherbone** checks the packet version and 32-bit address/data sizes. A record
  must contain exactly the base words and operations specified by its counts,
  fit `buffer_depth` words, have full byte masks and no errors, and use the
  supported incrementing address mode. Unsupported record flags are rejected.
  No Wishbone access is issued before record validation. Requests are serialized
  through reply completion to preserve the peer address/port. A Wishbone target
  must eventually acknowledge an issued transaction; this change adds no bus
  timeout.
- **DHCP** rejects early packet termination and errors in fixed fields, parses
  only enabled option bytes, and drains malformed packets before reporting an
  error. It does not consume a subsequent packet while recovering from an
  already accepted `last`.
- **UDP streamer RX** samples filtering at the first presented beat and holds
  that decision through the packet. Configuration changes affect subsequent
  packets, including when the first beat is stalled.

`PacketDropFIFO.discard` rejects a whole packet when asserted on any valid input
beat. The FIFO also drops overflowed packets, keeps its input ready while
receiving them, and exposes only committed complete packets. `drop` pulses once
at the rejected packet's end. Callers that need lossless backpressure must gate
new packets before entering this non-backpressuring FIFO.

## MAC ownership and ordering

TX formatting owns padding, CRC and preamble unless the PHY overrides their
configuration. `with_sys_datapath` selects whether formatting runs in `sys` or
`eth_tx`; CDC and width conversion surround that choice. A TX packet FIFO, when
enabled, holds the fully formatted frame in `eth_tx` before transmission.
Inter-frame gap insertion follows this FIFO, immediately before the PHY. A PHY
with `integrated_ifg_inserter` owns the gap instead. Buffering after gap insertion
would erase the idle cycles and is therefore invalid.

A PHY that cannot pause mid-frame requires an uninterrupted TX packet. Complete
packet buffering provides that guarantee up to the configured maximum frame
size; an upstream cut-through producer must otherwise guarantee the required
rate itself. Oversized TX packets are outside that configured contract.

`with_store_and_forward="auto"` uses a link-rate heuristic. Likewise crossbar
pipelining uses a clock-frequency heuristic for timing. These are configuration
defaults, not proofs of underrun safety or timing closure. Select explicit
buffering when required by the PHY and upstream traffic, and perform timing
analysis for the target device. RX buffering absorbs finite stalls and drops
whole packets on overflow; it cannot provide unlimited lossless buffering.

Keep protocol FSMs for actual transactions, parsing and error recovery. Thin
TX wrappers around LiteX `Packetizer` need no second framing FSM when headers
are aligned and parameters remain valid through output completion. Avoid a
shared protocol framework where a small local state machine explains the
wire behavior more clearly.
