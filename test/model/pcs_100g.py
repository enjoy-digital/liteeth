#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""Block-level reference for Clause 82 100GBASE-R without FEC (not synthesizable).

Bits are represented in transmission order: bit 0 is sent first. Inputs are already 64b/66b
encoded. Scrambling precedes 20-lane distribution; alignment markers bypass scrambling.
See doc/100g_pcs.md for scope, sources and the deliberately conservative acquisition model.
"""

LANES       = 20
AM_INTERVAL = 16383 # Data blocks per lane between markers.
MASK_58     = (1 << 58) - 1

# IEEE 802.3 Table 82-2, also reproduced in ITU-T G.709 Annex E, Table E.2.
MARKERS = [
    (0xc1, 0x68, 0x21), (0x9d, 0x71, 0x8e), (0x59, 0x4b, 0xe8), (0x4d, 0x95, 0x7b),
    (0xf5, 0x07, 0x09), (0xdd, 0x14, 0xc2), (0x9a, 0x4a, 0x26), (0x7b, 0x45, 0x66),
    (0xa0, 0x24, 0x76), (0x68, 0xc9, 0xfb), (0xfd, 0x6c, 0x99), (0xb9, 0x91, 0x55),
    (0x5c, 0xb9, 0xb2), (0x1a, 0xf8, 0xbd), (0x83, 0xc7, 0xca), (0x35, 0x36, 0xcd),
    (0xc4, 0x31, 0x4c), (0xad, 0xd6, 0xb7), (0x5f, 0x66, 0x2a), (0xc0, 0xf0, 0xe5),
]


def marker(lane, bip=0):
    if not 0 <= lane < LANES or not 0 <= bip < 256:
        raise ValueError("Invalid lane or BIP byte")
    low = bytes((*MARKERS[lane], bip))
    data = low + bytes(x ^ 0xff for x in low)
    return (int.from_bytes(data, "little") << 2) | 1 # Control header: first bit 1, then 0.


def marker_lane(block):
    if block is None or block & 3 != 1:
        return None
    data = (block >> 2).to_bytes(8, "little")
    if data[4:7] != bytes(x ^ 255 for x in data[:3]):
        return None
    # BIP does not participate in lane identification; it measures errors, not alignment.
    try:
        return MARKERS.index(tuple(data[:3]))
    except ValueError:
        return None


def bip8(block):
    # Table 82-4: XOR the eight payload octets; SH bits join parity bits 3 and 4.
    value = ((block & 1) << 3) | (((block >> 1) & 1) << 4)
    for offset in range(2, 66, 8):
        value ^= (block >> offset) & 255
    return value


class Scrambler:
    def __init__(self, descramble=False, state=MASK_58):
        self.state = state
        self.descramble = descramble

    def block(self, block):
        result = block & 3
        for offset in range(2, 66):
            incoming = (block >> offset) & 1
            outgoing = incoming ^ ((self.state >> 38) & 1) ^ ((self.state >> 57) & 1)
            self.state = ((self.state << 1) | (incoming if self.descramble else outgoing)) & MASK_58
            result |= outgoing << offset
        return result


def distribute(blocks, interval=AM_INTERVAL):
    """Stripe pre-scrambled blocks and insert AM/BIP; no idle deletion or rate adaptation."""
    if interval < 1 or len(blocks) % LANES:
        raise ValueError("Provide complete 20-block groups and a positive marker interval")
    lanes = [[] for _ in range(LANES)]
    parity = [0]*LANES
    for n, block in enumerate(blocks):
        if not 0 <= block < 1 << 66 or block & 3 not in [1, 2]:
            raise ValueError("Invalid 66-bit block")
        lane = n % LANES
        lanes[lane].append(block)
        parity[lane] ^= bip8(block)
        if (n//LANES + 1) % interval == 0:
            am = marker(lane, parity[lane])
            lanes[lane].append(am)
            parity[lane] = bip8(am) # The next BIP includes this marker.
    return lanes


def multiplex(lanes):
    """20:4 bit mux, using canonical group p, p+4, ..., p+16 for physical lane p."""
    if len(lanes) != LANES or len({len(lane) for lane in lanes}) != 1:
        raise ValueError("Expected twenty equally sized PCS lanes")
    physical = [[] for _ in range(4)]
    for n in range(len(lanes[0])):
        for bit in range(66):
            for p in range(4):
                physical[p].extend((lanes[p + 4*j][n] >> bit) & 1 for j in range(5))
    return physical


def demultiplex(physical):
    """Inverse of multiplex; input starts at a known 330-bit group boundary."""
    if len(physical) != 4 or len({len(lane) for lane in physical}) != 1 or len(physical[0]) % 330:
        raise ValueError("Expected four complete bit streams")
    lanes = [[] for _ in range(LANES)]
    for offset in range(0, len(physical[0]), 330):
        for p in range(4):
            for j in range(5):
                bits = physical[p][offset + j:offset + 330:5]
                if any(bit not in [0, 1] for bit in bits):
                    raise ValueError("PMA samples must be bits")
                lanes[p + 4*j].append(sum(bit << i for i, bit in enumerate(bits)))
    return lanes


def recover(lanes, interval=AM_INTERVAL, max_skew=32):
    """Recover intervals bracketed by two valid markers on every lane.

    This batch oracle drops incomplete/acquisition intervals and reacquires after bad markers.
    Skew is in 66-bit blocks; None samples represent initial lane delay. Bit/block-lock state
    machines and the standard's marker-error hysteresis are outside this model.
    Returns recovered scrambled blocks and the number of erroneous BIP bits.
    """
    if len(lanes) != LANES or interval < 1 or not 0 <= 2*max_skew < interval + 1:
        raise ValueError("Invalid lane count, interval or skew bound")
    period = interval + 1
    candidates = []
    for n, block in enumerate(lanes[0]):
        if marker_lane(block) is not None:
            candidates.append(n)
    result, errors = [], 0
    for start in candidates:
        positions = []
        for lane in lanes:
            matches = [(i, marker_lane(lane[i]))
                for i in range(max(0, start - max_skew), min(len(lane) - period, start + max_skew + 1))
                if marker_lane(lane[i]) is not None and
                   marker_lane(lane[i]) == marker_lane(lane[i + period])]
            if len(matches) != 1:
                break
            positions.append(matches[0])
        if len(positions) != LANES or len({ident for _, ident in positions}) != LANES:
            continue
        if max(i for i, _ in positions) - min(i for i, _ in positions) > max_skew:
            continue
        ordered = [None]*LANES
        for lane, (position, ident) in zip(lanes, positions):
            data = lane[position + 1:position + period]
            if any(block is None for block in data):
                break
            ordered[ident] = data
        if any(data is None for data in ordered):
            continue
        for lane, (position, _) in zip(lanes, positions):
            parity = 0
            for block in lane[position:position + period]:
                parity ^= bip8(block)
            received = (lane[position + period] >> 26) & 255
            complement = (lane[position + period] >> 58) & 255
            errors += bin(parity ^ received).count("1")
            errors += bin((received ^ 255) ^ complement).count("1")
        result.extend(block for row in zip(*ordered) for block in row)
    return result, errors


def main():
    import argparse
    import json
    import random
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=int, default=AM_INTERVAL)
    parser.add_argument("--periods", type=int, default=2)
    args = parser.parse_args()
    if args.periods < 2 or args.interval < 1:
        parser.error("Use at least two periods and a positive interval")
    prng = random.Random(42)
    scrambler = Scrambler()
    blocks = [(prng.getrandbits(64) << 2) | 2 for _ in range(args.periods*args.interval*LANES)]
    lanes = distribute([scrambler.block(block) for block in blocks], args.interval)
    Path(args.output).write_text(json.dumps(dict(mode="100gbase-r-no-fec", first_bit="lsb",
        interval=args.interval, encoded_blocks=[f"{block:017x}" for block in blocks],
        lanes=[[f"{block:017x}" for block in lane] for lane in lanes])) + "\n")


if __name__ == "__main__":
    main()
