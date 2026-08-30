#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Scott Torborg <scott@quadraturecat.com>
# SPDX-License-Identifier: BSD-2-Clause

import random
import unittest

from functools import lru_cache

from migen import *

from litex.gen import LiteXModule
from litex.gen.sim import run_simulation

from liteeth.common import eth_phy_description, eth_preamble
from liteeth.mac.core import LiteEthMACCore
from liteeth.phy.pcs_10g import PCS
from liteeth.phy.pcs_10g.ber_mon import PCSRXBERMonitor
from liteeth.phy.pcs_10g.block_sync import PCSRXFrameSync
from liteeth.phy.pcs_10g.common import *
from liteeth.phy.pcs_10g.decoder import XGMIIBaseRDecoder
from liteeth.phy.pcs_10g.encoder import XGMIIBaseREncoder
from liteeth.phy.pcs_10g.rx import PCSRX
from liteeth.phy.pcs_10g.tx import PCSTX
from liteeth.phy.pcs_10g.prbs import PRBS31Checker, PRBS31Generator
from liteeth.phy.pcs_10g.scrambler import Scrambler, Descrambler
from liteeth.phy.pcs_10g.watchdog import PCSRXWatchdog
from liteeth.phy.xgmii import LiteEthPHYXGMII

from test.model.mac import MACPacket
from test.stream_helpers import Packet, PacketLogger, PacketStreamer, check

# Helpers ------------------------------------------------------------------------------------------

def xgmii(*lanes):
    """Build a 64-bit XGMII word from eight lane characters, lane 0 in the low byte."""
    return int.from_bytes(bytes(lanes), "little")

def run_cycles(dut, inputs, outputs):
    """Drive per-cycle input sequences through a module and sample its outputs each cycle.

    `inputs` maps a signal to the sequence of values it takes, `outputs` a name to the signal
    sampled under it. Returns one dict per cycle, entry n holding the state after n cycles.

    Each signal's first value is placed in its reset rather than written, because a write only
    lands on the following cycle and these modules count the cycles they are given.
    """
    length = max(len(values) for values in inputs.values())
    seqs   = {sig: list(values) + [values[-1]]*(length + 1 - len(values))
              for sig, values in inputs.items()}

    for sig, seq in seqs.items():
        sig.reset = C(seq[0], len(sig))

    samples = []

    def sample():
        row = {}
        for name, sig in outputs.items():
            row[name] = yield sig
        samples.append(row)

    def generator():
        for n in range(length):
            yield from sample()
            for sig, seq in seqs.items():
                yield sig.eq(seq[n + 1])
            yield
        yield from sample()

    run_simulation(dut, generator())
    return samples

def words_to_bits(words, dw=64):
    """Flatten 64-bit words into transmission order (49.2.4.2: bit 0 of a word goes first)."""
    return [(word >> i) & 1 for word in words for i in range(dw)]

def random_words(n, dw=64, seed=0):
    rng = random.Random(seed)
    return [rng.getrandbits(dw) for _ in range(n)]

def run_stream(dut, words, initial_state=None):
    """Feed one word per cycle through a scrambler/descrambler and collect its output."""
    out = []

    def generator():
        if initial_state is not None:
            yield dut.state.eq(initial_state)
            yield
        for word in words:
            yield dut.data_in.eq(word)
            yield
            out.append((yield dut.data_out))

    run_simulation(dut, generator())
    return out

# Scrambler ----------------------------------------------------------------------------------------

SCRAMBLER_ORDER = 58 # G(x) = 1 + x^39 + x^58 (49.2.6), so the state spans 58 bits.

class TestScrambler(unittest.TestCase):
    def test_polynomial(self):
        """The output must satisfy out[n] = in[n] ^ out[n-39] ^ out[n-58] (49.2.6, Figure 49-8)."""
        words = random_words(64)
        din   = words_to_bits(words)
        dout  = words_to_bits(run_stream(Scrambler(), words))

        for n in range(SCRAMBLER_ORDER, len(din)):
            self.assertEqual(dout[n] ^ dout[n - 39] ^ dout[n - 58], din[n],
                f"scrambler polynomial violated at bit {n}")

    def test_descrambler_polynomial(self):
        """The descrambler must satisfy out[n] = in[n] ^ in[n-39] ^ in[n-58] (49.2.10)."""
        words = random_words(64, seed=1)
        din   = words_to_bits(words)
        dout  = words_to_bits(run_stream(Descrambler(), words))

        for n in range(SCRAMBLER_ORDER, len(din)):
            self.assertEqual(dout[n], din[n] ^ din[n - 39] ^ din[n - 58],
                f"descrambler polynomial violated at bit {n}")

    def test_roundtrip(self):
        """Descrambling a scrambled stream must return it, from any descrambler state.

        The descrambler is self-synchronizing: it takes its state from the received stream rather
        than being initialised in step with the far end, so the first 58 bits it emits are whatever
        its starting state made of them and every bit after that is exact.
        """
        words     = random_words(64, seed=2)
        scrambled = run_stream(Scrambler(), words)
        expected  = words_to_bits(words)

        for state in (0, 2**58 - 1, 0x2aaaaaaaaaaaaaa):
            recovered = words_to_bits(run_stream(Descrambler(), scrambled, initial_state=state))
            self.assertEqual(recovered[SCRAMBLER_ORDER:], expected[SCRAMBLER_ORDER:],
                f"descrambler never synchronized from state {state:#x}")

    def test_idle_is_scrambled(self):
        """A constant input must not produce a constant output.

        The scrambler exists to guarantee transition density (49.2.6); an all-zero XGMII idle
        pattern is the case that matters, since a link is idle most of the time.
        """
        out = run_stream(Scrambler(), [0]*64)
        self.assertGreater(len(set(out)), 32, "constant input produced a repetitive output")

# 64B/66B Blocks -----------------------------------------------------------------------------------

def block(*fields):
    """Pack (value, width) fields into a 64-bit block payload, first field in the low bits.

    Figure 49-7 draws each block left to right starting with the block type field, which is
    transmitted first and so occupies the low bits.
    """
    payload = 0
    offset  = 0
    for value, width in fields:
        payload |= (value & (2**width - 1)) << offset
        offset  += width
    assert offset == 64, f"block is {offset} bits, not 64"
    return payload

# The Figure 49-7 control block formats, each written out as the figure draws it. D<n>, C<n> and
# O<n> refer to XGMII lane n; the widths are 8 for a data character, 7 for a control code and 4 for
# an O code. Each entry maps the eight lane characters and their control codes onto a block.
CTRL_BLOCKS = {
    #                     BT                     fields...
    BLOCK_TYPE_CTRL     : lambda d, c: block((BLOCK_TYPE_CTRL,     8), *[(c[i], 7) for i in range(8)]),
    BLOCK_TYPE_OS_4     : lambda d, c: block((BLOCK_TYPE_OS_4,     8), *[(c[i], 7) for i in range(4)], (O_SEQ_OS, 4), *[(d[i], 8) for i in range(5, 8)]),
    BLOCK_TYPE_START_4  : lambda d, c: block((BLOCK_TYPE_START_4,  8), *[(c[i], 7) for i in range(4)], (0,        4), *[(d[i], 8) for i in range(5, 8)]),
    BLOCK_TYPE_OS_START : lambda d, c: block((BLOCK_TYPE_OS_START, 8), *[(d[i], 8) for i in range(1, 4)], (O_SEQ_OS, 4), (0,        4), *[(d[i], 8) for i in range(5, 8)]),
    BLOCK_TYPE_OS_04    : lambda d, c: block((BLOCK_TYPE_OS_04,    8), *[(d[i], 8) for i in range(1, 4)], (O_SEQ_OS, 4), (O_SEQ_OS, 4), *[(d[i], 8) for i in range(5, 8)]),
    BLOCK_TYPE_START_0  : lambda d, c: block((BLOCK_TYPE_START_0,  8), *[(d[i], 8) for i in range(1, 8)]),
    BLOCK_TYPE_OS_0     : lambda d, c: block((BLOCK_TYPE_OS_0,     8), *[(d[i], 8) for i in range(1, 4)], (O_SEQ_OS, 4), *[(c[i], 7) for i in range(4, 8)]),
    BLOCK_TYPE_TERM_0   : lambda d, c: block((BLOCK_TYPE_TERM_0,   8), (0, 7), *[(c[i], 7) for i in range(1, 8)]),
    BLOCK_TYPE_TERM_1   : lambda d, c: block((BLOCK_TYPE_TERM_1,   8), (d[0], 8), (0, 6), *[(c[i], 7) for i in range(2, 8)]),
    BLOCK_TYPE_TERM_2   : lambda d, c: block((BLOCK_TYPE_TERM_2,   8), *[(d[i], 8) for i in range(2)], (0, 5), *[(c[i], 7) for i in range(3, 8)]),
    BLOCK_TYPE_TERM_3   : lambda d, c: block((BLOCK_TYPE_TERM_3,   8), *[(d[i], 8) for i in range(3)], (0, 4), *[(c[i], 7) for i in range(4, 8)]),
    BLOCK_TYPE_TERM_4   : lambda d, c: block((BLOCK_TYPE_TERM_4,   8), *[(d[i], 8) for i in range(4)], (0, 3), *[(c[i], 7) for i in range(5, 8)]),
    BLOCK_TYPE_TERM_5   : lambda d, c: block((BLOCK_TYPE_TERM_5,   8), *[(d[i], 8) for i in range(5)], (0, 2), *[(c[i], 7) for i in range(6, 8)]),
    BLOCK_TYPE_TERM_6   : lambda d, c: block((BLOCK_TYPE_TERM_6,   8), *[(d[i], 8) for i in range(6)], (0, 1), (c[7], 7)),
    BLOCK_TYPE_TERM_7   : lambda d, c: block((BLOCK_TYPE_TERM_7,   8), *[(d[i], 8) for i in range(7)]),
}

def expected_block(block_type, lanes):
    """The block Figure 49-7 requires for the given XGMII lane characters."""
    codes = [CTRL_CODES.get(lane, CTRL_ERROR) for lane in lanes]
    return CTRL_BLOCKS[block_type](lanes, codes)

# Encoder ------------------------------------------------------------------------------------------

def run_encoder(vectors):
    """Drive one XGMII transfer per cycle and collect (hdr, data, bad) for each."""
    dut     = XGMIIBaseREncoder()
    samples = run_cycles(dut,
        inputs  = {
            dut.xgmii_txd : [txd for txd, _ in vectors],
            dut.xgmii_txc : [txc for _, txc in vectors],
        },
        outputs = {
            "hdr"  : dut.encoded_tx_hdr,
            "data" : dut.encoded_tx_data,
            "bad"  : dut.tx_bad_block,
        },
    )
    # The encoder registers its output, so the block for transfer n is readable one cycle later.
    return [(s["hdr"], s["data"], s["bad"]) for s in samples[1:]]

# XGMII lane patterns, one per Figure 49-7 control block format, plus the data block.
D = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]
I = XGMII_IDLE

ENCODER_VECTORS = [
    #  name                lanes                                             txc     block type
    ("data",               D,                                                0x00,   None),
    ("all idle",           [I]*8,                                            0xff,   BLOCK_TYPE_CTRL),
    ("ordered set lane 4", [I, I, I, I, XGMII_SEQ_OS] + D[5:],               0x1f,   BLOCK_TYPE_OS_4),
    ("start lane 4",       [I, I, I, I, XGMII_START]  + D[5:],               0x1f,   BLOCK_TYPE_START_4),
    ("ordered set/start",  [XGMII_SEQ_OS] + D[1:4] + [XGMII_START] + D[5:],  0x11,   BLOCK_TYPE_OS_START),
    ("ordered sets 0/4",   [XGMII_SEQ_OS] + D[1:4] + [XGMII_SEQ_OS] + D[5:], 0x11,   BLOCK_TYPE_OS_04),
    ("start lane 0",       [XGMII_START]  + D[1:],                           0x01,   BLOCK_TYPE_START_0),
    ("ordered set lane 0", [XGMII_SEQ_OS] + D[1:4] + [I]*4,                  0xf1,   BLOCK_TYPE_OS_0),
    ("terminate lane 0",   [XGMII_TERM] + [I]*7,                             0xff,   BLOCK_TYPE_TERM_0),
    ("terminate lane 1",   D[:1] + [XGMII_TERM] + [I]*6,                     0xfe,   BLOCK_TYPE_TERM_1),
    ("terminate lane 2",   D[:2] + [XGMII_TERM] + [I]*5,                     0xfc,   BLOCK_TYPE_TERM_2),
    ("terminate lane 3",   D[:3] + [XGMII_TERM] + [I]*4,                     0xf8,   BLOCK_TYPE_TERM_3),
    ("terminate lane 4",   D[:4] + [XGMII_TERM] + [I]*3,                     0xf0,   BLOCK_TYPE_TERM_4),
    ("terminate lane 5",   D[:5] + [XGMII_TERM] + [I]*2,                     0xe0,   BLOCK_TYPE_TERM_5),
    ("terminate lane 6",   D[:6] + [XGMII_TERM] + [I],                       0xc0,   BLOCK_TYPE_TERM_6),
    ("terminate lane 7",   D[:7] + [XGMII_TERM],                             0x80,   BLOCK_TYPE_TERM_7),
    ("all low power idle", [XGMII_LPI]*8,                                    0xff,   BLOCK_TYPE_CTRL),
    ("all error",          [XGMII_ERROR]*8,                                  0xff,   BLOCK_TYPE_CTRL),
]

class TestEncoder(unittest.TestCase):
    def test_block_formats(self):
        """Every Figure 49-7 block format must be produced with the fields the figure specifies."""
        vectors = [(xgmii(*lanes), txc) for _, lanes, txc, _ in ENCODER_VECTORS]
        results = run_encoder(vectors)

        for (name, lanes, txc, block_type), (hdr, data, bad) in zip(ENCODER_VECTORS, results):
            with self.subTest(name):
                if block_type is None:
                    # 49.2.4.3: a data block carries the XGMII characters unchanged.
                    self.assertEqual(hdr,  SYNC_DATA)
                    self.assertEqual(data, xgmii(*lanes))
                else:
                    self.assertEqual(hdr,  SYNC_CTRL)
                    self.assertEqual(data, expected_block(block_type, lanes),
                        f"{data:#018x} is not the block Figure 49-7 specifies")
                self.assertFalse(bad, "a legal XGMII transfer was flagged as a bad block")

    def test_control_codes(self):
        """Each XGMII control character must map to its Table 49-1 control code."""
        vectors = [(xgmii(*[char]*8), 0xff) for char in CTRL_CODES]
        results = run_encoder(vectors)

        for char, (hdr, data, bad) in zip(CTRL_CODES, results):
            with self.subTest(f"{char:#04x}"):
                self.assertEqual(hdr, SYNC_CTRL)
                self.assertEqual(data, block((BLOCK_TYPE_CTRL, 8), *[(CTRL_CODES[char], 7)]*8))
                self.assertFalse(bad)

    def test_bad_blocks(self):
        """A transfer with no Figure 49-7 block format must be flagged and encoded as error.

        49.2.4.4 has the encoder emit an error block for anything it cannot represent, so that the
        far end sees /E/ rather than a plausible-looking frame.
        """
        error_block = block((BLOCK_TYPE_CTRL, 8), *[(CTRL_ERROR, 7)]*8)
        vectors = [
            # Terminate is only legal as the first control character of a transfer.
            (xgmii(*(D[:4] + [XGMII_TERM] + D[5:])), 0x10),
            # Start is only legal in lane 0 or lane 4 (46.3.1.4).
            (xgmii(*(D[:2] + [XGMII_START] + D[3:])), 0x04),
            # A control character with no Table 49-1 encoding.
            (xgmii(*[0xaa]*8), 0xff),
        ]
        for hdr, data, bad in run_encoder(vectors):
            self.assertEqual(hdr,  SYNC_CTRL)
            self.assertEqual(data, error_block)
            self.assertTrue(bad, "an unencodable transfer was not flagged")

    def test_bad_control_character_in_valid_format(self):
        """A block format that fits, but with an unencodable control character, is still bad."""
        # Terminate in lane 3, but lane 5 holds a character with no control code.
        lanes = D[:3] + [XGMII_TERM, I, 0xaa, I, I]
        (hdr, data, bad), = run_encoder([(xgmii(*lanes), 0xf8)])

        self.assertEqual(hdr, SYNC_CTRL)
        self.assertEqual(data, expected_block(BLOCK_TYPE_TERM_3, lanes))
        self.assertTrue(bad, "an unencodable control character was not flagged")

# Decoder ------------------------------------------------------------------------------------------

def run_decoder(blocks):
    """Drive one 66-bit block per cycle and collect (rxd, rxc, bad, seq_error) for each."""
    dut     = XGMIIBaseRDecoder()
    samples = run_cycles(dut,
        inputs  = {
            dut.encoded_rx_hdr  : [hdr  for hdr, _    in blocks],
            dut.encoded_rx_data : [data for _,   data in blocks],
        },
        outputs = {
            "rxd" : dut.xgmii_rxd,
            "rxc" : dut.xgmii_rxc,
            "bad" : dut.rx_bad_block,
            "seq" : dut.rx_sequence_error,
        },
    )
    # The decoder registers its output, so the transfer for block n is readable one cycle later.
    return [(s["rxd"], s["rxc"], s["bad"], s["seq"]) for s in samples[1:]]

ERROR_LANES = xgmii(*[XGMII_ERROR]*8)

class TestDecoder(unittest.TestCase):
    def test_block_formats(self):
        """Every Figure 49-7 block format must decode back to the XGMII transfer it carries."""
        blocks = [
            (SYNC_DATA, xgmii(*lanes)) if bt is None else (SYNC_CTRL, expected_block(bt, lanes))
            for _, lanes, _, bt in ENCODER_VECTORS
        ]
        results = run_decoder(blocks)

        for (name, lanes, txc, _), (rxd, rxc, bad, _) in zip(ENCODER_VECTORS, results):
            with self.subTest(name):
                self.assertEqual(rxd, xgmii(*lanes))
                self.assertEqual(rxc, txc)
                self.assertFalse(bad, "a valid block was flagged as bad")

    def test_control_codes(self):
        """Each Table 49-1 control code must decode to its XGMII control character."""
        blocks = [
            (SYNC_CTRL, block((BLOCK_TYPE_CTRL, 8), *[(code, 7)]*8))
            for code in XGMII_CHARS
        ]
        for code, (rxd, rxc, bad, _) in zip(XGMII_CHARS, run_decoder(blocks)):
            with self.subTest(f"{code:#04x}"):
                self.assertEqual(rxd, xgmii(*[XGMII_CHARS[code]]*8))
                self.assertEqual(rxc, 0xff)
                self.assertFalse(bad)

    def test_signal_ordered_set(self):
        """/Fsig/ is an O code of Table 49-1, so a block carrying it is valid (49.2.4.6).

        Table 49-1 defines 0xf as the signal ordered set, and 49.2.13.2.3 counts a block holding
        "a valid O code" as type C, so /Fsig/ must reach XGMII as 0x5c rather than being decoded to
        /E/ the way an O code outside the table is.
        """
        lanes  = [I, I, I, I, XGMII_SIG_OS] + D[5:]
        blk    = block((BLOCK_TYPE_OS_4, 8), *[(CTRL_IDLE, 7)]*4, (O_SIG_OS, 4),
                       *[(d, 8) for d in D[5:]])
        (rxd, rxc, bad, _), = run_decoder([(SYNC_CTRL, blk)])

        self.assertEqual(rxd, xgmii(*lanes))
        self.assertEqual(rxc, 0x1f)
        self.assertFalse(bad, "a valid signal ordered set was flagged as a bad block")

    def test_invalid_blocks(self):
        """Anything not in Figure 49-7 must decode to /E/ on every lane and be flagged.

        49.2.4.6 lists what makes a block invalid: a sync header that is not 01 or 10, a block type
        field not in the figure, or an O code not in Table 49-1.
        """
        blocks = [
            ("sync header 00",  (0b00,      expected_block(BLOCK_TYPE_CTRL, [I]*8))),
            ("sync header 11",  (0b11,      expected_block(BLOCK_TYPE_CTRL, [I]*8))),
            ("block type 0x00", (SYNC_CTRL, block((0x00, 8), (0, 56)))),
        ]
        for (name, _), (rxd, rxc, bad, _) in zip(blocks, run_decoder([b for _, b in blocks])):
            with self.subTest(name):
                self.assertEqual(rxd, ERROR_LANES)
                self.assertEqual(rxc, 0xff)
                self.assertTrue(bad, "an invalid block was not flagged")

    def test_invalid_ordered_set(self):
        """An O code not in Table 49-1 makes the block invalid (49.2.4.6)."""
        blk = block((BLOCK_TYPE_OS_4, 8), *[(CTRL_IDLE, 7)]*4, (0x5, 4),
                    *[(d, 8) for d in D[5:]])
        (rxd, rxc, bad, _), = run_decoder([(SYNC_CTRL, blk)])

        self.assertEqual(rxd, xgmii(*([I]*4 + [XGMII_ERROR] + D[5:])))
        self.assertTrue(bad, "an invalid O code was not flagged")

    def test_invalid_control_code(self):
        """A control code not in Table 49-1 must decode to /E/ in its lane and be flagged."""
        codes = [CTRL_IDLE]*8
        codes[5] = 0x01
        (rxd, rxc, bad, _), = run_decoder(
            [(SYNC_CTRL, block((BLOCK_TYPE_CTRL, 8), *[(c, 7) for c in codes]))])

        self.assertEqual(rxd, xgmii(*([I]*5 + [XGMII_ERROR] + [I]*2)))
        self.assertEqual(rxc, 0xff)
        self.assertTrue(bad)

    def test_sequence_errors(self):
        """Start inside a frame, and terminate outside one, must raise rx_sequence_error."""
        ctrl  = (SYNC_CTRL, expected_block(BLOCK_TYPE_CTRL,   [I]*8))
        start = (SYNC_CTRL, expected_block(BLOCK_TYPE_START_0, [XGMII_START] + D[1:]))
        data  = (SYNC_DATA, xgmii(*D))
        term  = (SYNC_CTRL, expected_block(BLOCK_TYPE_TERM_7,  D[:7] + [XGMII_TERM]))

        # A well formed frame, then a second start with no terminate between them, then two
        # terminates in a row.
        blocks   = [ctrl, start, data, term, ctrl, start, data, start, data, term, term]
        expected = [0,    0,     0,    0,    0,    0,     0,    1,     0,    0,    1]

        for n, (rxd, rxc, bad, seq) in enumerate(run_decoder(blocks)):
            with self.subTest(n):
                self.assertEqual(seq, expected[n])
                self.assertFalse(bad, "a valid block was flagged as bad")

    def test_roundtrip(self):
        """Encoding an XGMII transfer and decoding it back must return the transfer."""
        vectors = [(xgmii(*lanes), txc) for _, lanes, txc, _ in ENCODER_VECTORS]
        encoded = run_encoder(vectors)
        decoded = run_decoder([(hdr, data) for hdr, data, _ in encoded])

        for (name, lanes, txc, _), (rxd, rxc, bad, _) in zip(ENCODER_VECTORS, decoded):
            with self.subTest(name):
                self.assertEqual(rxd, xgmii(*lanes))
                self.assertEqual(rxc, txc)
                self.assertFalse(bad)

# Block Synchronization ----------------------------------------------------------------------------

SH_INVALID = 0b00 # Neither 01 nor 10, so it cannot occur on an aligned stream (49.2.4.6 a).

def run_frame_sync(headers, **kwargs):
    """Feed one sync header per cycle and collect (block_lock, bitslip) after each."""
    dut = PCSRXFrameSync(**kwargs)
    samples = run_cycles(dut,
        inputs  = {dut.serdes_rx_hdr : headers},
        outputs = {"lock" : dut.rx_block_lock, "slip" : dut.serdes_rx_bitslip},
    )
    return [(s["lock"], s["slip"]) for s in samples]

def valid_headers(n):
    """n sync headers that alternate control and data, as an idle stream does."""
    return [SYNC_CTRL if i % 2 else SYNC_DATA for i in range(n)]

class TestBlockSync(unittest.TestCase):
    def test_lock_requires_64_valid_headers(self):
        """Block lock is only declared after 64 consecutive valid sync headers (Figure 49-12).

        Figure 49-12 leaves LOCK_INIT only via 64_GOOD, which requires sh_cnt to reach 64 with
        sh_invalid_cnt still zero.
        """
        # 63 valid headers followed by an invalid one must never lock.
        lock = [lock for lock, _ in run_frame_sync(valid_headers(63) + [SH_INVALID])]
        self.assertNotIn(1, lock, "block lock declared on fewer than 64 valid sync headers")

        # 64 must, and block lock is visible on the cycle after the 64th.
        lock = [lock for lock, _ in run_frame_sync(valid_headers(80))]
        self.assertEqual(lock.index(1), 64,
            "block lock was not declared on the 64th consecutive valid sync header")

    def test_invalid_header_slips_while_unlocked(self):
        """An invalid sync header before lock requests a bit slip and restarts the count.

        Figure 49-12 takes LOCK_INIT and RESET_CNT straight to SLIP on INVALID_SH: while unlocked
        there is no error budget, since the candidate boundary is simply wrong.
        """
        headers  = valid_headers(10) + [SH_INVALID] + valid_headers(100)
        results  = run_frame_sync(headers)
        bitslips = [n for n, (_, slip) in enumerate(results) if slip]

        self.assertEqual(len(bitslips), 1, "an invalid header before lock did not request one slip")
        # The 10 valid headers before the slip must not count towards the 64 needed after it.
        self.assertNotIn(1, [lock for lock, _ in results[:bitslips[0] + 64]],
            "the header count was not restarted by the slip")
        self.assertIn(1, [lock for lock, _ in results], "the receiver never recovered lock")

    def test_locked_tolerates_up_to_15_invalid_headers(self):
        """Once locked, a window survives 15 invalid sync headers and fails on the 16th.

        49.2.13.2.4 sets sh_invalid_cnt's threshold at 16: below it Figure 49-12 returns to
        VALID_SH, at it the state machine goes to SLIP and block_lock is cleared.
        """
        for invalid, expect_lock in ((15, True), (16, False)):
            with self.subTest(invalid=invalid):
                # Each invalid header is followed by a valid one so they land in a single window,
                # 64 headers wide, rather than spilling into the next.
                window   = [h for _ in range(invalid) for h in (SH_INVALID, SYNC_DATA)]
                headers  = valid_headers(64) + window + valid_headers(8)
                results  = run_frame_sync(headers)

                self.assertIn(1, [lock for lock, _ in results], "the receiver never locked")
                self.assertEqual(results[-1][0], expect_lock,
                    f"block lock was {'lost' if expect_lock else 'held'} on {invalid} invalid "
                    f"sync headers in one window")

    def test_bitslip_timing(self):
        """A slip request is held for bitslip_high_cycles and followed by bitslip_low_cycles idle.

        49.2.13.2.3 leaves the slip method to the implementation, but the transceiver needs the
        request held long enough to see it and needs time to settle before its output is judged.
        """
        for high, low in ((1, 8), (2, 4), (3, 12)):
            with self.subTest(high=high, low=low):
                results  = run_frame_sync([SH_INVALID]*64,
                    bitslip_high_cycles = high,
                    bitslip_low_cycles  = low,
                )
                slips = [slip for _, slip in results]

                # A steady stream of invalid headers slips continuously, so the waveform is a
                # repeating high/low pattern once the first request has been made.
                start = slips.index(1)
                period = slips[start:start + high + low]
                self.assertEqual(period, [1]*high + [0]*low)

# BER Monitor --------------------------------------------------------------------------------------

WINDOW = 64 # Stands in for the 125 us window, so a test runs in a readable number of cycles.

def run_ber_mon(headers, block_lock=1, test_mode=0):
    """Feed one sync header per cycle and collect hi_ber after each."""
    dut = PCSRXBERMonitor(count_125us=WINDOW)
    samples = run_cycles(dut,
        inputs  = {
            dut.serdes_rx_hdr : headers,
            dut.block_lock    : [block_lock],
            dut.test_mode     : [test_mode],
        },
        outputs = {"hi_ber" : dut.rx_high_ber},
    )
    return [s["hi_ber"] for s in samples]

def with_invalid(n, total):
    """A stream of `total` sync headers of which `n` are invalid, each followed by a valid one."""
    headers = [h for _ in range(n) for h in (SH_INVALID, SYNC_DATA)]
    return headers + valid_headers(total - len(headers))

class TestBERMonitor(unittest.TestCase):
    def test_high_ber_threshold(self):
        """hi_ber asserts on the 16th invalid sync header of a window, and not before.

        49.2.13.2.2 sets ber_cnt's threshold at 16; Figure 49-13 reaches HI_BER only from
        BER_BAD_SH with ber_cnt already at 15.
        """
        for invalid, expect in ((15, False), (16, True)):
            with self.subTest(invalid=invalid):
                hi_ber = run_ber_mon(with_invalid(invalid, WINDOW))
                self.assertEqual(any(hi_ber), expect,
                    f"hi_ber {'stayed clear' if expect else 'asserted'} on {invalid} invalid sync "
                    f"headers in one window")

    def test_good_window_clears_high_ber(self):
        """A window that ends below the threshold clears hi_ber (Figure 49-13, GOOD_BER)."""
        headers = with_invalid(16, WINDOW) + valid_headers(2*WINDOW)
        hi_ber  = run_ber_mon(headers)

        self.assertIn(1, hi_ber, "hi_ber never asserted")
        self.assertFalse(hi_ber[-1], "a clean window did not clear hi_ber")

    def test_held_in_init(self):
        """Without block lock, or in test-pattern mode, the monitor stays in BER_MT_INIT.

        Figure 49-13 holds BER_MT_INIT while block_lock is false or test_mode is true, so hi_ber
        cannot assert however many invalid sync headers arrive -- and a PRBS31 stream has no valid
        sync headers at all.
        """
        headers = [SH_INVALID]*(4*WINDOW)
        self.assertNotIn(1, run_ber_mon(headers, block_lock=0),
            "hi_ber asserted without block lock")
        self.assertNotIn(1, run_ber_mon(headers, test_mode=1),
            "hi_ber asserted in test-pattern mode")

# Watchdog -----------------------------------------------------------------------------------------

WINDOWS = 20 # Enough for rx_status to qualify, which takes 16 consecutive good windows.

def run_watchdog(headers, block_lock=1, high_ber=0):
    """Feed one sync header per cycle and collect (rx_status, reset_req) after each.

    block_lock and high_ber take either a constant or a per-cycle sequence.
    """
    def seq(value):
        return value if isinstance(value, list) else [value]

    dut = PCSRXWatchdog(count_125us=WINDOW)
    samples = run_cycles(dut,
        inputs  = {
            dut.serdes_rx_hdr : headers,
            dut.rx_block_lock : seq(block_lock),
            dut.rx_high_ber   : seq(high_ber),
        },
        outputs = {"status" : dut.rx_status, "reset_req" : dut.serdes_rx_reset_req},
    )
    return [(s["status"], s["reset_req"]) for s in samples]

class TestWatchdog(unittest.TestCase):
    def test_status_qualifies_on_a_good_link(self):
        """rx_status asserts once the link has been good for long enough, and stays asserted."""
        status = [s for s, _ in run_watchdog(valid_headers(WINDOWS*WINDOW))]

        self.assertIn(1, status, "rx_status never qualified on a clean stream")
        self.assertEqual(status[-1], 1, "rx_status did not stay asserted")

    def test_status_requires_block_lock(self):
        """49.2.14.1: PCS_status "is only true if block_lock is true"."""
        lock   = [1]*(WINDOWS*WINDOW) + [0]*WINDOW
        status = [s for s, _ in run_watchdog(valid_headers(len(lock)), block_lock=lock)]

        self.assertIn(1, status, "rx_status never qualified")
        self.assertEqual(status[-1], 0, "rx_status stayed asserted after block lock was lost")

    def test_status_requires_low_ber(self):
        """49.2.14.1: PCS_status "is only true if [...] hi_ber is false"."""
        ber    = [0]*(WINDOWS*WINDOW) + [1]*WINDOW
        status = [s for s, _ in run_watchdog(valid_headers(len(ber)), high_ber=ber)]

        self.assertIn(1, status, "rx_status never qualified")
        self.assertEqual(status[-1], 0, "rx_status stayed asserted while hi_ber was set")

    def test_status_requires_control_blocks(self):
        """A window with no control sync header is not a good window.

        A link carrying only data blocks is not idling between frames as a conformant link partner
        must (46.3.1.4), so it cannot be trusted even though its sync headers are all valid.
        """
        results = run_watchdog([SYNC_DATA]*(WINDOWS*WINDOW))

        self.assertNotIn(1, [s for s, _ in results], "rx_status qualified without control blocks")
        self.assertIn(1, [r for _, r in results],
            "the transceiver was never asked to reset after 16 bad windows")

# PRBS31 -------------------------------------------------------------------------------------------

PRBS31_BLOCK = 66 # The test pattern replaces the whole block, sync header included (49.2.8).

def prbs31_generate(count):
    """The first `count` words of the PRBS31 test pattern."""
    dut     = PRBS31Generator(PRBS31_BLOCK)
    samples = run_cycles(dut, {dut.enable : [1]*count}, {"data" : dut.data_out})
    return [s["data"] for s in samples[:count]]

def prbs31_check(words):
    """Run words through the PRBS31 checker and return its per-bit error flags."""
    dut     = PRBS31Checker(PRBS31_BLOCK)
    samples = run_cycles(dut,
        inputs  = {dut.enable : [1]*len(words), dut.data_in : list(words)},
        outputs = {"errors" : dut.data_out},
    )
    return [s["errors"] for s in samples[:len(words)]]

class TestPRBS31(unittest.TestCase):
    def test_polynomial(self):
        """The pattern must satisfy G(x) = 1 + x^28 + x^31, inverted (49.2.8, Figure 49-9).

        49.2.8 specifies the inverted version of the polynomial's bit stream, so where the raw
        sequence obeys b[n] = b[n-28] ^ b[n-31], the transmitted one obeys its complement.
        """
        bits = words_to_bits(prbs31_generate(32), dw=PRBS31_BLOCK)

        for n in range(31, len(bits)):
            self.assertEqual(bits[n] ^ bits[n - 28] ^ bits[n - 31], 1,
                f"PRBS31 polynomial violated at bit {n}")

    def test_checker_accepts_clean_pattern(self):
        """The checker must report no errors on the generator's own output.

        49.2.12's checker is self-synchronizing, so its first 31 bits are whatever its initial
        state made of them and every bit after that must be error free.
        """
        errors = words_to_bits(prbs31_check(prbs31_generate(32)), dw=PRBS31_BLOCK)
        self.assertNotIn(1, errors[31:], "the checker reported errors on a clean PRBS31 stream")

    def test_checker_reports_three_errors_per_bit(self):
        """An isolated bit error raises the error signal three times (49.2.12).

        The bit is compared once as it is received and once as it passes each of the two taps, so
        the error count runs at three times the bit error rate for isolated errors.
        """
        words    = prbs31_generate(32)
        words[8] ^= 1 << 20 # A single flipped bit, far from either end of the stream.

        errors = words_to_bits(prbs31_check(words), dw=PRBS31_BLOCK)
        self.assertEqual(sum(errors[31:]), 3,
            "an isolated bit error was not reported exactly three times")

    def test_generator_parks_while_disabled(self):
        """The pattern only advances while test-pattern mode is selected.

        Parking the state means enabling the mode always restarts the pattern from the same point,
        so a measurement is repeatable.
        """
        dut     = PRBS31Generator(PRBS31_BLOCK)
        samples = run_cycles(dut, {dut.enable : [0]*4 + [1]*4}, {"data" : dut.data_out})
        data    = [s["data"] for s in samples]

        self.assertEqual(len(set(data[:5])), 1, "the pattern advanced while disabled")
        self.assertEqual(len(set(data[5:])), len(data[5:]), "the pattern did not advance")

# PCS Loopback -------------------------------------------------------------------------------------

# LBLOCK_R, the vector the receive process emits in RX_INIT: "two Local Fault ordered_sets"
# (49.2.13.2.3). A Local Fault ordered set is /Q/ followed by 0x00, 0x00, 0x01 (46.3.4), so lanes 0
# and 4 hold the ordered set and the other six hold data.
LOCAL_FAULT_DATA = xgmii(XGMII_SEQ_OS, 0x00, 0x00, 0x01, XGMII_SEQ_OS, 0x00, 0x00, 0x01)
LOCAL_FAULT_CTRL = 0x11

class PCSLoopbackDUT(LiteXModule):
    """A PCS with its transmit serdes interface wired back to its receive one.

    That is what a transceiver in near-end loopback presents, minus the transceiver. `bit_offset`
    additionally misaligns the block boundary, so the receiver has to slip its way back to it.
    """
    def __init__(self, dw=64, bit_offset=None):
        # A loopback has one clock: there is no separately recovered receive clock.
        #
        # The 125 us window and the transceiver's slip settling period are both shortened to what
        # the state machines need to be exercised, since neither carries any meaning in simulation
        # and rx_status costs 16 consecutive windows.
        self.pcs = pcs = ClockDomainsRenamer({"eth_tx" : "sys", "eth_rx" : "sys"})(PCS(
            dw                 = dw,
            count_125us        = 8,
            bitslip_low_cycles = 1,
        ))

        if bit_offset is None:
            self.comb += [
                pcs.serdes_rx_data.eq(pcs.serdes_tx_data),
                pcs.serdes_rx_hdr.eq(pcs.serdes_tx_hdr),
            ]
            return

        # A misaligned gearbox: the receiver is handed a 66-bit window taken `bit_offset` bits into
        # the transmitted stream, so the sync headers land in the wrong place. Each slip request
        # advances the window by one bit and wraps at the block boundary, which satisfies
        # 49.2.13.2.3's only requirement -- that every bit position can be reached.
        width = dw + 2

        current = Signal(width)
        previous = Signal(width)
        window   = Signal(2*width)
        received = Signal(width)
        offset   = Signal(max=width, reset=bit_offset)

        self.comb += current.eq(Cat(pcs.serdes_tx_hdr, pcs.serdes_tx_data))
        self.sync += previous.eq(current)

        self.comb += [
            # Older word in the low bits, so shifting right slides the window forward.
            window.eq(Cat(previous, current)),
            received.eq(window >> offset),
            pcs.serdes_rx_hdr.eq(received[0:2]),
            pcs.serdes_rx_data.eq(received[2:width]),
        ]

        self.sync += If(pcs.serdes_rx_bitslip,
            If(offset == width - 1, offset.eq(0)).Else(offset.eq(offset + 1)),
        )

PCS_LATENCY     = 4   # Registers on the way round: encoder, transmit interface, descrambler, decoder.
LOOPBACK_CYCLES = 400 # Enough for an aligned receiver to reach PCS_status.
SLIP_CYCLES     = 500 # Enough for a misaligned one to walk to the block boundary first.

XGMII_IDLE_XFER = (xgmii(*[XGMII_IDLE]*8), 0xff)

def xgmii_frame(start_lane):
    """A legal XGMII frame. 46.3.1.4 allows Start only in lane 0 or lane 4."""
    if start_lane == 0:
        start = (xgmii(XGMII_START, *D[1:]),                              0x01)
        term  = (xgmii(*(D[:4] + [XGMII_TERM] + [XGMII_IDLE]*3)),         0xf0)
    else:
        start = (xgmii(*([XGMII_IDLE]*4 + [XGMII_START] + D[5:])),        0x1f)
        term  = (xgmii(*(D[:7] + [XGMII_TERM])),                          0x80)
    return [start] + [(xgmii(*D), 0x00)]*4 + [term]

def xgmii_stimulus(cycles):
    """An idle stream with frames in it, as a conformant link partner transmits.

    Both Start positions appear: a sender leaving generous gaps only ever starts in lane 0, so a
    lane 4 Start -- and the Figure 49-7 block format that carries it -- would otherwise never be
    exercised end to end.
    """
    pattern = ([XGMII_IDLE_XFER]*8 + xgmii_frame(start_lane=0)
             + [XGMII_IDLE_XFER]*4 + xgmii_frame(start_lane=4) + [XGMII_IDLE_XFER]*4)
    return (pattern*(cycles//len(pattern) + 1))[:cycles]

@lru_cache(maxsize=None)
def run_pcs_loopback(cycles, bit_offset=None):
    """Drive XGMII into a looped-back PCS and record what comes back, cycle by cycle.

    Cached, because a full PCS is the slowest thing here to simulate and several tests read
    different things out of the same run.
    """
    dut       = PCSLoopbackDUT(bit_offset=bit_offset)
    transfers = xgmii_stimulus(cycles)

    return transfers, run_cycles(dut,
        inputs  = {
            dut.pcs.xgmii_txd : [txd for txd, _ in transfers],
            dut.pcs.xgmii_txc : [txc for _, txc in transfers],
        },
        outputs = {
            "rxd"    : dut.pcs.xgmii_rxd,
            "rxc"    : dut.pcs.xgmii_rxc,
            "lock"   : dut.pcs.rx_block_lock,
            "status" : dut.pcs.rx_status,
            "bad"    : dut.pcs.rx_bad_block,
        },
    )

class TestPCSLoopback(unittest.TestCase):
    def find_latency(self, transfers, samples):
        """The pipeline delay at which received XGMII reproduces transmitted XGMII."""
        up   = next(n for n, s in enumerate(samples) if s["status"])
        tail = range(up + 1, len(samples))

        for latency in range(16):
            if all(samples[n]["rxd"] == transfers[n - latency][0] and
                   samples[n]["rxc"] == transfers[n - latency][1] for n in tail):
                return latency
        self.fail("received XGMII never reproduced the transmitted XGMII at any delay")

    def test_local_fault_before_block_lock(self):
        """Until block lock, the receive process emits Local Fault ordered sets.

        Figure 49-15 holds RX_INIT while block_lock is false, and 49.2.13.2.3 has RX_INIT drive
        LBLOCK_R -- two Local Fault ordered sets -- onto XGMII, so the MAC above sees a faulted
        link rather than whatever the unaligned stream happened to decode to.
        """
        transfers, samples = run_pcs_loopback(LOOPBACK_CYCLES)
        locked = next(n for n, s in enumerate(samples) if s["lock"])

        self.assertGreater(locked, 0, "the PCS reported block lock immediately")
        for n in range(locked):
            self.assertEqual(samples[n]["rxd"], LOCAL_FAULT_DATA,
                f"cycle {n} did not carry a Local Fault ordered set before block lock")
            self.assertEqual(samples[n]["rxc"], LOCAL_FAULT_CTRL)

    def test_loopback(self):
        """Once the link is up, XGMII in must equal XGMII out."""
        transfers, samples = run_pcs_loopback(LOOPBACK_CYCLES)

        self.assertTrue(samples[-1]["status"], "the PCS never reached PCS_status")
        self.assertEqual(self.find_latency(transfers, samples), PCS_LATENCY)

    def test_bitslip_alignment(self):
        """A receiver started off the block boundary must slip its way onto it.

        49.2.13.2.3 requires only that every bit position can be reached, so a receiver handed a
        window at any offset into the stream must still arrive at block lock.
        """
        for bit_offset in (1, 33, 65):
            with self.subTest(bit_offset=bit_offset):
                transfers, samples = run_pcs_loopback(SLIP_CYCLES, bit_offset=bit_offset)

                self.assertTrue(samples[-1]["status"],
                    f"the PCS never locked from a {bit_offset}-bit offset")
                # The sliding window costs one register of its own on top of the PCS pipeline.
                self.assertEqual(self.find_latency(transfers, samples), PCS_LATENCY + 1)

    def test_no_bad_blocks_once_up(self):
        """A link at PCS_status carrying legal XGMII must never report a bad block."""
        transfers, samples = run_pcs_loopback(LOOPBACK_CYCLES)
        up = next(n for n, s in enumerate(samples) if s["status"])

        self.assertNotIn(1, [s["bad"] for s in samples[up:]],
            "a bad block was reported on a clean loopback")

# Receive Process ----------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def pcs_tx_stream(cycles):
    """The serdes words a PCS transmitter produces for `cycles` of XGMII stimulus."""
    transfers = xgmii_stimulus(cycles)
    dut       = PCSTX()
    samples   = run_cycles(dut,
        inputs  = {
            dut.xgmii_txd : [txd for txd, _ in transfers],
            dut.xgmii_txc : [txc for _, txc in transfers],
        },
        outputs = {"hdr" : dut.serdes_tx_hdr, "data" : dut.serdes_tx_data},
    )
    # Two register stages fill before the first transfer's block reaches the serdes.
    return transfers, [(s["hdr"], s["data"]) for s in samples[2:]]

def run_pcs_rx(blocks):
    """Drive serdes words into the receive path and record the XGMII it produces."""
    dut = PCSRX(count_125us=8, bitslip_low_cycles=1)
    return run_cycles(dut,
        inputs  = {
            dut.serdes_rx_hdr  : [hdr  for hdr, _    in blocks],
            dut.serdes_rx_data : [data for _,   data in blocks],
        },
        outputs = {"rxd" : dut.xgmii_rxd, "rxc" : dut.xgmii_rxc, "bad" : dut.rx_bad_block},
    )

class TestReceiveProcess(unittest.TestCase):
    def test_error_block_on_data_where_control_belongs(self):
        """A data block arriving where the receive process expects control yields EBLOCK_R.

        Figure 49-15 leaves RX_C for RX_E on anything but C or S, and 49.2.13.2.3 has RX_E drive
        EBLOCK_R -- /E/ in all eight character locations -- onto XGMII. Flipping one sync header
        from control to data is the case that only the state machine can catch: the block is still
        well formed, decodes cleanly as data, and raises no bad-block flag, so a receiver without
        Figure 49-15 would pass the scrambler's idea of an idle straight up to the MAC.
        """
        transfers, blocks = pcs_tx_stream(300)

        # An idle well after block lock, so the receive process is settled in RX_C. The sync header
        # bypasses the scrambler (49.2.4.3), so flipping it leaves the payload untouched.
        n = next(i for i in range(200, len(blocks)) if transfers[i] == XGMII_IDLE_XFER)
        corrupted = list(blocks)
        corrupted[n] = (corrupted[n][0] ^ 0b11, corrupted[n][1])

        clean   = run_pcs_rx(blocks)
        faulted = run_pcs_rx(corrupted)

        differing = [i for i in range(len(clean))
                     if (clean[i]["rxd"], clean[i]["rxc"]) != (faulted[i]["rxd"], faulted[i]["rxc"])]

        self.assertEqual(len(differing), 1,
            "flipping one sync header disturbed more than the block it belongs to")
        self.assertEqual(faulted[differing[0]]["rxd"], ERROR_LANES,
            "the receive process did not substitute an error block")
        self.assertEqual(faulted[differing[0]]["rxc"], 0xff)
        self.assertFalse(faulted[differing[0]]["bad"],
            "the block decoded badly, so this does not test the receive process")

# PHY over the PCS ---------------------------------------------------------------------------------

class PHYOverPCS(LiteXModule):
    """LiteEth's XGMII PHY with the PCS behind it, looped back at the serdes.

    The PHY's stream endpoints are left exposed, so whatever is connected to them decides how much
    of the stack is under test.
    """
    def __init__(self, dw=64):
        self.xgmii = Record([
            ("rx_ctl",  dw//8),
            ("rx_data", dw),
            ("tx_ctl",  dw//8),
            ("tx_data", dw),
        ])

        self.phy = ClockDomainsRenamer({"eth_tx" : "sys", "eth_rx" : "sys"})(LiteEthPHYXGMII(
            clock_pads = Record([("rx", 1), ("tx", 1)]),
            pads       = self.xgmii,
            model      = True,
            dw         = dw,
        ))
        self.loopback = loopback = PCSLoopbackDUT(dw=dw)

        self.comb += [
            loopback.pcs.xgmii_txd.eq(self.xgmii.tx_data),
            loopback.pcs.xgmii_txc.eq(self.xgmii.tx_ctl),
            self.xgmii.rx_data.eq(loopback.pcs.xgmii_rxd),
            self.xgmii.rx_ctl.eq(loopback.pcs.xgmii_rxc),
        ]

        self.sink, self.source = self.phy.sink, self.phy.source

class StreamPCSLoopbackDUT(LiteXModule):
    """A packet stream straight onto the PHY, with no MAC in between.

    Nothing in the path adds an FCS or padding, so a packet must come back exactly as it went in.
    That makes this the narrowest end-to-end test of the pair -- the stream-to-XGMII framing and
    the PCS, with nothing else a failure could be attributed to.
    """
    def __init__(self, dw=64):
        self.link     = link = PHYOverPCS(dw)
        self.streamer = PacketStreamer(eth_phy_description(dw), byte_data=True)
        self.logger   = PacketLogger(eth_phy_description(dw), byte_data=True)

        self.comb += [
            self.streamer.source.connect(link.sink),
            link.source.connect(self.logger.sink),
        ]

def run_over_pcs(dut, packets):
    """Wait for the link to come up, then send packets through it and collect what returns."""
    results = []
    status  = []

    def generator():
        for _ in range(LOOPBACK_CYCLES):
            status.append((yield dut.link.loopback.pcs.rx_status))
            if status[-1]:
                break
            yield

        for packet in packets:
            dut.streamer.send(packet)
            yield from dut.logger.receive(timeout=1500)
            # Copied per packet: the logger reuses its packet object for the next one.
            results.append((packet, list(dut.logger.packet)))

    run_simulation(dut, [generator(), dut.streamer.generator(), dut.logger.generator()])
    return status, results

def phy_packet(length, seed):
    """A packet as the PHY's stream expects it, preamble included.

    The XGMII transmitter substitutes /S/ for the first preamble octet and the receiver matches on
    the whole word to find a frame (46.3.1.1), so the preamble is part of the PHY's stream contract
    -- above it, that is what the MAC supplies.
    """
    return Packet(list(eth_preamble.to_bytes(8, "little"))
                + [(seed + i) & 0xff for i in range(length)])

class TestStreamPCSLoopback(unittest.TestCase):
    def test_packets_survive_the_pcs(self):
        """A packet framed onto XGMII and put through the PCS must come back byte for byte."""
        dut = StreamPCSLoopbackDUT()
        # 64 bytes fills whole bus words; 60 leaves a partial one, so last_be has to survive the
        # trip out to XGMII and back.
        packets = [phy_packet(length, seed) for seed, length in enumerate((64, 60))]

        status, results = run_over_pcs(dut, packets)

        self.assertTrue(status and status[-1], "the PCS never reached PCS_status")
        self.assertEqual(len(results), len(packets), "not every packet came back")

        for sent, received in results:
            with self.subTest(length=len(sent)):
                self.assertEqual(received, list(sent))

# MAC over the PCS ---------------------------------------------------------------------------------

class MACPCSLoopbackDUT(LiteXModule):
    """LiteEth's MAC on top of the same PHY and PCS.

    The MAC adds the preamble, padding and FCS, so this covers what the stream-level test cannot:
    that a real frame survives, and that LiteEth's XGMII receiver tolerates the Local Fault ordered
    sets the PCS drives at it until it has block lock.
    """
    def __init__(self, dw=64):
        self.link = link = PHYOverPCS(dw)
        self.core = core = ClockDomainsRenamer({"eth_tx" : "sys", "eth_rx" : "sys"})(
            LiteEthMACCore(phy=link.phy, dw=dw))

        self.streamer = PacketStreamer(eth_phy_description(dw), byte_data=True)
        self.logger   = PacketLogger(eth_phy_description(dw), byte_data=True)

        self.comb += [
            self.streamer.source.connect(core.sink),
            core.source.connect(self.logger.sink),
        ]

def mac_frame(length, seed):
    packet = MACPacket([(seed + i) & 0xff for i in range(length)])
    packet.target_mac    = 0x010203040506
    packet.sender_mac    = 0x090a0b0c0d0e
    packet.ethernet_type = 0x0800
    packet.encode_header()
    return packet

class TestMACPCSLoopback(unittest.TestCase):
    def test_frames_survive_the_pcs(self):
        """A frame sent by the MAC must come back byte for byte through the whole PCS."""
        dut     = MACPCSLoopbackDUT()
        packets = [mac_frame(length, seed=n) for n, length in enumerate((46, 100))]

        status, results = run_over_pcs(dut, packets)

        self.assertTrue(status and status[-1], "the PCS never reached PCS_status")
        self.assertEqual(len(results), len(packets), "not every frame came back")

        for sent, received in results:
            with self.subTest(length=len(sent)):
                # A frame the MAC dropped on an FCS failure arrives as nothing at all, so check
                # that before comparing: check() raises IndexError on an empty packet.
                self.assertTrue(received, "the frame never came back through the PCS")
                shift, _, errors = check(sent, received)
                self.assertEqual(errors, 0, "payload mismatch")
                self.assertEqual(shift,  0, "frame misaligned")
                self.assertEqual(len(received), len(sent), "frame length changed")


if __name__ == "__main__":
    unittest.main()
