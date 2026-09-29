# Native 100G PCS reference

`test/model/pcs_100g.py` models Clause 82 100GBASE-R without FEC: twenty logical
PCS lanes transported over four NRZ lanes at 25.78125 Gb/s. A matching physical
use case is a 100GBASE-LR4 module over CAUI-4. The model is not synthesizable.
SR4 and CR4/KR4 require Clause 91 RS-FEC; PAM4 modes require their specified
transcoding, FEC and PMA mapping.

## Data path

The model accepts already encoded 66-bit blocks, with bit zero transmitted first:

1. Scramble the 64 payload bits using the Clause 49 polynomial; bypass sync headers.
2. Distribute blocks round-robin over twenty logical lanes.
3. Insert a lane-specific alignment marker after every 16,383 data blocks per lane.
   Markers bypass scrambling. BIP covers the preceding marker and intervening
   scrambled blocks, including the assigned sync-header bits.
4. Bit-multiplex five logical lanes per physical lane. The model maps physical
   lane `p` to logical lanes `p, p+4, ..., p+16`.

RX identifies, reorders and deskews lanes, removes markers, combines the block
stream, then descrambles. The aggregate scrambler and twenty logical lanes
prevent treating this mode as four independent 25G PCS instances. The future
native implementation should retain the existing frame interface and the
[PHY ownership boundaries](phy_portability.md); the [CMAC adapter](100g.md)
provides that frame interface using vendor MAC/PCS/PMA instead.

## Receive model limits

`recover()` releases intervals bracketed by two matching markers on every lane.
It discards acquisition/incomplete intervals and reacquires after corruption.
Skew is measured in 66-bit blocks and must be less than half a marker period.
BIP reports erroneous bit positions without changing alignment; either redundant
copy can flag a position, which counts once. Initial parity starts at zero;
the first marker establishes history and is not checked.

The batch model omits cycle-level block/marker-lock hysteresis, BER/fault state
machines, idle insertion/deletion for marker overhead, clock tolerance, MAC
control-block encoding/decoding and RS-FEC. PMA demultiplexing assumes a known
mux phase and block boundary. These functions, independent stream comparisons,
and board timing/link validation remain necessary for a native hardware PHY.

## Tests and vectors

```sh
python3 -m pytest -q test/test_pcs_100g_model.py
python3 -m test.model.pcs_100g --output vectors.json
```

Tests check the published lane-zero marker vector, every BIP bit assignment,
real marker spacing, lane/bit ordering, scrambler synchronization, skew,
corruption, deleted blocks, reacquisition and missing/duplicate lanes.

The exporter produces deterministic encoded blocks and lane streams. Use
`--interval` for shorter development fixtures; the default is 16,383.
Generated vectors depend on this model; the published marker and BIP cases
provide independent reference checks.

## Sources

- IEEE Std 802.3-2012, Section Six: Clauses 82.2.5–82.2.9, Tables 82-2 and 82-4,
  the transmitted-bit example in 82.2.8, and Clause 83.5.2.
- [ITU-T G.709 Annex E, Table E.2](https://www.itu.int/en/publications/documents/tsb/2017-5g_basics/files/basic-html/page1272.html):
  lane-marker encodings.
- [AMD PG203 alignment-marker spacing](https://docs.amd.com/r/en-US/pg203-cmac-usplus/Alignment-Marker-Spacing):
  normal marker interval.
- [IEEE CAUI-4/FEC discussion](https://www.ieee802.org/3/50G/email/msg00088.html):
  no-FEC and RS(528,514) transport over CAUI-4.
