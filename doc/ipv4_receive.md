# IPv4 receive validation

LiteEth receives IPv4 without options or fragment reassembly: version 4,
IHL 5, a valid header checksum, no fragment offset and no More Fragments flag.
The total length must exceed the 20-byte header because native LiteEth streams
require a nonempty payload. Invalid and empty lengths are rejected before the
header length is subtracted, including when Ethernet padding follows the header.
Rejected packets are drained to their physical end before accepting the next.

The user endpoint's `length` is the declared IPv4 payload length in bytes.
The existing receive path forwards physical payload bytes including Ethernet
padding; transport consumers such as UDP use the declared length to trim it.
This change does not add fragment reassembly or payload checksum checking.

## Destination policy

`with_broadcast=False` accepts only the configured local destination IP.
The default `with_broadcast=True` historically bypasses destination-IP filtering
entirely. It also admits multicast, DHCP replies addressed to an offered IP,
and other destination addresses. This permissive behavior is preserved; the
option is not a filter limited to local unicast and the broadcast address.
MAC destination filtering remains a separate layer. Integrations needing strict
IPv4 destination filtering should select `with_broadcast=False` and account for
the resulting exclusion of multicast and DHCP traffic addressed elsewhere.
