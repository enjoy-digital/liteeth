# Native 100G PCS preparation

The first reference mode is **Clause 82 100GBASE-R without FEC**, transported as
four NRZ lanes at 25.78125 Gb/s. A suitable physical use case is a 100GBASE-LR4
module reached over CAUI-4. This is a reference model and test-vector foundation,
not a native FPGA PHY or an interoperability claim.

Do not treat "100G" as one encoding. LR4/ER4, SR4, CR4/KR4 and newer PAM4 links
have different PMD and FEC requirements. In particular, SR4 and CR4/KR4 require
the appropriate Clause 91 RS-FEC path; this no-FEC model cannot drive them as a
compliant implementation. PAM4 modes also need their specified transcoding, FEC,
alignment and PMA mapping. Auto-negotiation and link training are separate work.

## Reusable boundaries

The frame-facing MAC stream stays `data/be/error/last` with ready/valid. Protocol
cores do not learn about PCS lanes or vendor IP. A hardware CMAC can own MAC,
PCS and PMA, with the portable frame adapter supplying buffering. A later native
PCS instead connects to a suitably wide soft MAC. These are two implementations
of the frame boundary, not two protocol stacks.

The native sequence to validate is:

1. Encode blocks and control characters, then scramble the 64 payload bits with
   the Clause 49 polynomial. Sync headers bypass the common scrambler.
2. Distribute successive blocks round-robin over twenty logical PCS lanes.
3. Insert a lane-specific alignment marker after every 16,383 data blocks per
   lane. Markers bypass scrambling. Their BIP covers the preceding marker and
   intervening scrambled data, including the assigned sync-header bits.
4. Bit-multiplex five logical lanes onto each physical lane. On RX, recover bit
   and block boundaries, identify/reorder lanes, deskew, remove markers, combine
   the block stream, then descramble and decode.

The current single-lane PCS's scrambler polynomial and coding concepts are
reusable. A 100G PCS is **not** four independent 25G PCS instances: it has twenty
logical lanes, one aggregate scrambled block stream and explicit alignment.
Keep lane distribution, alignment, FEC and vendor transceiver setup separately
owned, following [PHY portability](phy_portability.md).

## Reference model and tests

`test/model/pcs_100g.py` accepts already encoded blocks. Bit zero is transmitted
first. It implements the scrambler/descrambler, lane distribution, real alignment
markers, BIP, a canonical 20:4 bit mux and a bounded deskew/reorder oracle.
The canonical physical lane `p` carries logical lanes `p, p+4, ..., p+16`;
receivers must identify logical lanes rather than assume this ordering.

The configured skew bound must be less than half a marker period, so adjacent
marker groups cannot overlap in its search window. The batch receive oracle releases only intervals bracketed by two matching
markers on every lane. It discards acquisition/incomplete intervals and
reacquires after corruption. This conservative test policy deliberately does
not implement the standard's cycle-level block-lock, marker-lock hysteresis,
BER, fault-signalling or deskew state machines. BIP errors count erroneous bits;
they do not change alignment. Initial BIP accumulation starts at zero and the
first marker is used for acquisition, not checked against an unknown history.

The model does not delete/insert idles to compensate marker overhead, model
clock tolerance, encode/decode MAC control blocks, or implement RS-FEC. Its PMA
inverse assumes a known mux phase and 66-bit block boundary. None of these
omissions should be mistaken for a hardware implementation.

Run `python3 -m pytest -q test/test_pcs_100g_model.py`. Tests include:

- The published lane-zero marker bit sequence and every BIP bit assignment.
- The real 16,383-block marker interval, including BIP history.
- Every logical lane's bit-to-physical-lane/phase mapping.
- Scrambler self-synchronization, lane permutation, bounded skew and excessive skew.
- Payload corruption, lost/corrupted markers, reacquisition, duplicate IDs and missing lanes.

Generate deterministic fixtures for future RTL with
`python3 -m test.model.pcs_100g --output vectors.json`. Reduced marker intervals
are useful for development, but the default uses the standard interval. These
fixtures are model outputs; the independent published marker and BIP tests are
the reference checks, not a loopback through the same implementation.

## Before a native hardware implementation

Implement and independently verify Clause 82 receive state machines and idle
rate compensation. Compare complete encoded streams against a second PCS model
or vendor simulation. Select a board, PMD, reference clock and FEC mode before
adding PMA wrappers; constrain CDC/skew and run device timing analysis. Finally,
verify lane polarity/order, resets, clock tolerance, error recovery and sustained
traffic with a real peer. Software tests cannot establish those properties.

## Sources

- IEEE Std 802.3-2012, Section Six: Clauses 82.2.5–82.2.9, Table 82-2, Table 82-4,
  the transmitted-bit example in 82.2.8, and Clause 83.5.2. The same Clause 82
  boundaries remain the reference for the selected legacy no-FEC mode.
- [ITU-T G.709 Annex E, Table E.2](https://www.itu.int/en/publications/documents/tsb/2017-5g_basics/files/basic-html/page1272.html)
  reproduces the twenty lane-marker encodings.
- [AMD PG203 alignment-marker spacing](https://docs.amd.com/r/en-US/pg203-cmac-usplus/Alignment-Marker-Spacing)
  confirms the normal marker interval; simulation shortcuts must not become hardware settings.

- [IEEE CAUI-4/FEC discussion](https://www.ieee802.org/3/50G/email/msg00088.html)
  explains transporting both no-FEC and RS(528,514) modes over CAUI-4.
