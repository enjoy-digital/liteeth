# Byte-Enabled Packet Streams

LiteEth packet streams use `be`, one valid-byte bit per data byte, on every beat.
Bit zero qualifies the lowest eight data bits. Intermediate beats must enable
all bytes. The final beat has a nonzero contiguous mask starting at bit zero.
For a 64-bit packet ending with three bytes, use `last=1, be=0x07`; a full beat
uses `be=0xff`. Eight-bit producers drive `be=1` on every valid byte.

These masks can connect directly to AXI Stream `tkeep` within this packet format.
Sparse masks, zero masks and partial intermediate beats require compaction before
entering LiteEth; the MAC does not interpret zero as a full word. Payload byte
order, packet lengths and software-visible MAC CSRs are unchanged.

The mask follows data through FIFOs, clock-domain crossings, width conversion,
header insertion/removal, padding and CRC processing. Width conversion ends at
the final occupied slice and clears unused enables when widening. Consequently,
MAC last-marker correction stages and Etherbone's special 64-bit last handler
are no longer needed. Errors are qualified by valid lanes; errors in removed
FCS bytes still invalidate the final payload beat.

## Updating Integrations

Update LiteX first: LiteEth requires native byte-enable handling in its
`StrideConverter`, `Packetizer` and `Depacketizer`. See LiteX's
[`stream_byte_enables.md`](https://github.com/enjoy-digital/litex/blob/master/doc/stream_byte_enables.md).

Replace endpoint `last_be` accesses with `be` **and update their encoding**.
For `n` valid bytes the new mask is `(1 << n) - 1`; intermediate words use the
full mask, rather than zero. Update custom PHY receive endpoints as well.
Do not simply rename the old one-hot signal.

For code that needs a gradual migration, adapt the legacy endpoint explicitly:

```python
legacy_tx = stream.LastBEConverter(eth_udp_user_description(32))
self.submodules += legacy_tx
self.comb += legacy_tx.source.connect(udp_port.sink)
# Existing logic drives legacy_tx.sink.last_be; the UDP port carries be.
```

Use `reverse=True` for a receive boundary. Adapters preserve the old zero/full
convention. There are no implicit aliases between the two encodings.
The internal `LiteEthLastHandler`, `LiteEthMACTXLastBE` and `LiteEthMACRXLastBE`
stages have been removed; custom pipelines should use the updated stride
converter directly.

`LiteEthUDPStreamer`, `LiteEthStream2UDPTX` and `LiteEthUDP2StreamRX` expose the
native mask with `with_be=True`. Their default whole-word user interfaces stay
unchanged. Existing `with_last_be=True` calls retain legacy user endpoints using
boundary adapters; the UDP-side endpoints always use native `be`. The old
`tkeep2last_be`/`last_be2tkeep` helpers remain available for legacy integrations.

Etherbone's memory-mapped endpoint now calls its Wishbone byte-selection parameter
`byte_enable`. Its stream `be` qualifies the transported word. These have distinct
meanings: a full Etherbone word can request only selected Wishbone byte lanes.

## Standalone Generator

UDP stream ports retain `sink_keep`/`source_keep` pins, now wired directly to
`be`. Drive a full mask on every full word; leaving `sink_keep` at zero no longer
means a full word. For existing whole-word integrations, set `with_tkeep: False`
to omit the pins and generate full masks internally.

Raw UDP ports now expose `sink_be`/`source_be`. Set `with_last_be: True` on a raw
port to retain its old `sink_last_be`/`source_last_be` pins and encoding through
explicit adapters. Other raw-port pins are unchanged.
