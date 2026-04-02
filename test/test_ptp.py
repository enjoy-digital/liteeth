#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""PTP Core unit tests (HDL simulation)."""

import unittest

from migen import *
from litex.gen import *
from litex.gen.sim import *

from liteeth.common import *

from liteeth.core.ptp import *

# Helpers ------------------------------------------------------------------------------------------

# Master clock identity (8B clockIdentity + 2B portNumber).
MASTER_CLOCK_ID = 0xAABBCCDDEEFF0001
SLAVE_CLOCK_ID  = 0x10e2d5000001_0001

# Default IP addresses.
MASTER_IP       = 0xC0A80164  # 192.168.1.100
SLAVE_IP        = 0xC0A80132  # 192.168.1.50

SYS_CLK_FREQ    = 100_000_000

def _ts80(seconds, nanoseconds):
    """Build an 80-bit PTP timestamp (48-bit seconds || 32-bit nanoseconds)."""
    return (seconds << 32) | nanoseconds

def _build_ptp_header(msg_type, seq_id=0, domain=0, flags=0, source_port_id=MASTER_CLOCK_ID):
    """Build a 34-byte PTP header as a list of bytes (big-endian wire format)."""
    hdr = [0] * PTP_HEADER_LENGTH  # 34 bytes.

    # Byte 0: transport_specific(4) | msg_type(4).
    hdr[0] = (msg_type & 0x0F)

    # Byte 1: reserved(4) | version(4).
    hdr[1] = PTP_VERSION

    # Bytes 2-3: messageLength (34 header + 10 body = 44 for most messages).
    length = 44
    hdr[2] = (length >> 8) & 0xFF
    hdr[3] = (length >> 0) & 0xFF

    # Byte 4: domainNumber.
    hdr[4] = domain & 0xFF

    # Bytes 6-7: flags.
    hdr[6] = (flags >> 8) & 0xFF
    hdr[7] = (flags >> 0) & 0xFF

    # Bytes 8-15: correction (0).

    # Bytes 20-29: sourcePortIdentity (10 bytes, big-endian).
    for i in range(10):
        hdr[20 + i] = (source_port_id >> (8 * (9 - i))) & 0xFF

    # Bytes 30-31: sequenceId.
    hdr[30] = (seq_id >> 8) & 0xFF
    hdr[31] = (seq_id >> 0) & 0xFF

    return hdr

def _build_ptp_body_timestamp(seconds, nanoseconds):
    """Build a 10-byte PTP body timestamp (6B seconds + 4B nanoseconds, big-endian)."""
    body = []
    for i in range(6):
        body.append((seconds >> (8 * (5 - i))) & 0xFF)
    for i in range(4):
        body.append((nanoseconds >> (8 * (3 - i))) & 0xFF)
    return body

def _build_ptp_body_with_requester(seconds, nanoseconds, requester_id=SLAVE_CLOCK_ID):
    """Build a 20-byte PTP body: 10B timestamp + 10B requestingPortIdentity."""
    body = _build_ptp_body_timestamp(seconds, nanoseconds)
    for i in range(10):
        body.append((requester_id >> (8 * (9 - i))) & 0xFF)
    return body

def _build_ptp_packet(msg_type, body_bytes, seq_id=0, domain=0, flags=0,
                      source_port_id=MASTER_CLOCK_ID, ip_address=MASTER_IP):
    """Build a complete PTP packet as (data_bytes, ip_address, udp_port)."""
    hdr = _build_ptp_header(msg_type, seq_id=seq_id, domain=domain, flags=flags,
                            source_port_id=source_port_id)
    if msg_type in (PTP_MSG_SYNC, PTP_MSG_DELAY_REQ, PTP_MSG_PDELAY_REQ, PTP_MSG_PDELAY_RESP):
        udp_port = PTP_EVENT_PORT
    else:
        udp_port = PTP_GENERAL_PORT
    return hdr + body_bytes, ip_address, udp_port

class UDPPort:
    """Minimal UDP port mock with source/sink stream endpoints."""
    def __init__(self, dw=8):
        self.dw     = dw
        self.sink   = stream.Endpoint(eth_udp_user_description(dw))
        self.source = stream.Endpoint(eth_udp_user_description(dw))

def _send_udp_packet(port, data_bytes, ip_address=MASTER_IP, src_port=319, dst_port=319):
    """Generator: send a byte sequence into a UDP port's source endpoint."""
    for i, b in enumerate(data_bytes):
        yield port.source.valid.eq(1)
        yield port.source.data.eq(b)
        yield port.source.last.eq(1 if i == len(data_bytes) - 1 else 0)
        yield port.source.last_be.eq(1 if i == len(data_bytes) - 1 else 0)
        yield port.source.ip_address.eq(ip_address)
        yield port.source.src_port.eq(src_port)
        yield port.source.dst_port.eq(dst_port)
        yield
        while not (yield port.source.ready):
            yield
    yield port.source.valid.eq(0)
    yield

def _make_ptp_dut(timeout=0.001, monitor_debug=None):
    """Create a top-level PTP DUT with UDP ports."""
    event_port   = UDPPort()
    general_port = UDPPort()
    dut = LiteEthPTP(event_port, general_port, SYS_CLK_FREQ,
                     timeout=timeout, monitor_debug=monitor_debug)
    return dut, event_port, general_port

def _run_e2e_exchange(event_port, general_port, dut, seq, t1_sec, t1_ns, t4_sec, t4_ns,
                      fup_seq=None, dresp_requester=SLAVE_CLOCK_ID):
    """Generator: run one complete E2E exchange (Sync + FUp + wait + Delay_Resp)."""
    two_step_flag = 1 << PTP_TWO_STEP_FLAG_BIT

    # 1. Sync.
    sync_body = _build_ptp_body_timestamp(0, 0)
    sync_pkt, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, sync_body, seq_id=seq, flags=two_step_flag)
    yield from _send_udp_packet(event_port, sync_pkt, ip_address=ip,
                                src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
    for _ in range(20):
        yield

    # 2. Follow_Up.
    fup_body = _build_ptp_body_timestamp(t1_sec, t1_ns)
    fup_pkt, ip, _ = _build_ptp_packet(PTP_MSG_FOLLOW_UP, fup_body,
                                        seq_id=fup_seq if fup_seq is not None else seq)
    yield from _send_udp_packet(general_port, fup_pkt, ip_address=ip,
                                src_port=PTP_GENERAL_PORT, dst_port=PTP_GENERAL_PORT)

    # Wait for Delay_Req TX.
    for _ in range(500):
        yield

    # 3. Delay_Resp.
    dresp_body = _build_ptp_body_with_requester(t4_sec, t4_ns, dresp_requester)
    dresp_pkt, ip, _ = _build_ptp_packet(PTP_MSG_DELAY_RESP, dresp_body, seq_id=seq)
    yield from _send_udp_packet(general_port, dresp_pkt, ip_address=ip,
                                src_port=PTP_GENERAL_PORT, dst_port=PTP_GENERAL_PORT)

    # Wait for servo.
    for _ in range(200):
        yield

# Test TSU -----------------------------------------------------------------------------------------

class TestTSU(unittest.TestCase):
    """Verify TSU timestamp counting and latching."""

    def test_tsu_counts(self):
        """TSU nanoseconds should increment each cycle by the addend value."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            for _ in range(20):
                yield
            results["nanoseconds"] = (yield dut.nanoseconds)
            results["seconds"]     = (yield dut.seconds)

        run_simulation(dut, gen(dut))

        self.assertGreater(results["nanoseconds"], 0)
        self.assertEqual(results["seconds"], 0)

    def test_tsu_second_rollover(self):
        """TSU should roll nanoseconds at 1e9 and increment seconds."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            yield dut.step.eq(1)
            yield dut.step_target.eq(_ts80(0, 999_999_900))
            yield
            yield dut.step.eq(0)
            yield
            yield
            for _ in range(50):
                yield
            results["seconds"]     = (yield dut.seconds)
            results["nanoseconds"] = (yield dut.nanoseconds)

        run_simulation(dut, gen(dut))

        self.assertGreaterEqual(results["seconds"], 1)

    def test_tsu_latch(self):
        """TSU should latch RX/TX timestamps on latch pulse."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            for _ in range(50):
                yield
            yield dut.rx_latch.eq(1)
            yield
            yield dut.rx_latch.eq(0)
            yield
            yield
            results["rx_ts"] = (yield dut.rx_ts)

        run_simulation(dut, gen(dut))

        self.assertGreater(results["rx_ts"], 0)

    def test_tsu_offset_positive(self):
        """Positive offset should advance nanoseconds."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            # Step to a known time.
            yield dut.step.eq(1)
            yield dut.step_target.eq(_ts80(10, 500_000_000))
            yield
            yield dut.step.eq(0)
            yield
            yield
            # Apply +1000ns offset.
            yield dut.offset.eq(1000)
            yield
            yield
            yield
            results["nanoseconds"] = (yield dut.nanoseconds)
            results["seconds"]     = (yield dut.seconds)

        run_simulation(dut, gen(dut))

        # Nanoseconds should be around 500_001_000 + ticking.
        self.assertGreater(results["nanoseconds"], 500_001_000)
        self.assertEqual(results["seconds"], 10)

    def test_tsu_offset_negative(self):
        """Negative offset should retard nanoseconds."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            yield dut.step.eq(1)
            yield dut.step_target.eq(_ts80(10, 500_000_000))
            yield
            yield dut.step.eq(0)
            yield
            yield
            # Apply -1000ns offset.
            yield dut.offset.eq(-1000)
            yield
            yield
            yield
            results["nanoseconds"] = (yield dut.nanoseconds)
            results["seconds"]     = (yield dut.seconds)

        run_simulation(dut, gen(dut))

        self.assertLess(results["nanoseconds"], 500_000_000)
        self.assertEqual(results["seconds"], 10)

    def test_tsu_offset_seconds_boundary(self):
        """Offset crossing zero should borrow from seconds."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            yield dut.step.eq(1)
            yield dut.step_target.eq(_ts80(10, 100))
            yield
            yield dut.step.eq(0)
            yield
            yield
            # Offset that crosses zero: -200ns when at ~100ns.
            yield dut.offset.eq(-200)
            yield
            yield
            yield
            results["nanoseconds"] = (yield dut.nanoseconds)
            results["seconds"]     = (yield dut.seconds)

        run_simulation(dut, gen(dut))

        # Should borrow: seconds=9, nanoseconds near 1e9.
        self.assertEqual(results["seconds"], 9)
        self.assertGreater(results["nanoseconds"], 999_999_000)

    def test_tsu_addend_change(self):
        """Changing addend should affect tick rate."""
        dut = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        results = {}

        def gen(dut):
            # Run 100 cycles with default addend, record ns.
            for _ in range(100):
                yield
            ns_default = (yield dut.nanoseconds)

            # Reset via step and run with doubled addend.
            yield dut.step.eq(1)
            yield dut.step_target.eq(0)
            yield
            yield dut.step.eq(0)
            yield dut.addend.eq((yield dut.addend) * 2)
            yield
            yield
            for _ in range(100):
                yield
            ns_fast = (yield dut.nanoseconds)

            results["ns_default"] = ns_default
            results["ns_fast"]    = ns_fast

        run_simulation(dut, gen(dut))

        # Doubled addend should produce roughly double the nanosecond count.
        self.assertGreater(results["ns_fast"], results["ns_default"] * 1.5)

# Test RX ------------------------------------------------------------------------------------------

class TestPTPRX(unittest.TestCase):
    """Verify PTP RX depacketization and field extraction."""

    def _make_event_dut(self):
        class DUT(LiteXModule):
            def __init__(self):
                self.udp_port = UDPPort()
                self.rx = LiteEthPTPRX(PTP_EVENT_PORT, sys_clk_freq=SYS_CLK_FREQ)
                self.comb += [
                    self.udp_port.source.connect(self.rx.sink),
                    self.rx.domain.eq(0),
                ]
        return DUT()

    def _make_general_dut(self):
        class DUT(LiteXModule):
            def __init__(self):
                self.udp_port = UDPPort()
                self.rx = LiteEthPTPRX(PTP_GENERAL_PORT, sys_clk_freq=SYS_CLK_FREQ)
                self.comb += [
                    self.udp_port.source.connect(self.rx.sink),
                    self.rx.domain.eq(0),
                ]
        return DUT()

    def test_sync_reception(self):
        """RX should depacketize a Sync message and extract fields."""
        dut = self._make_event_dut()
        results = {}

        two_step_flag = 1 << PTP_TWO_STEP_FLAG_BIT
        body = _build_ptp_body_timestamp(100, 500_000_000)
        pkt_data, ip, _ = _build_ptp_packet(
            PTP_MSG_SYNC, body, seq_id=42, flags=two_step_flag)

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["present"]   = (yield dut.rx.present)
            results["msg_type"]  = (yield dut.rx.msg_type)
            results["seq_id"]    = (yield dut.rx.seq_id)
            results["two_step"]  = (yield dut.rx.two_step)
            results["timestamp"] = (yield dut.rx.timestamp)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"],  1)
        self.assertEqual(results["msg_type"], PTP_MSG_SYNC)
        self.assertEqual(results["seq_id"],   42)
        self.assertEqual(results["two_step"], 1)
        self.assertEqual(results["timestamp"], _ts80(100, 500_000_000))

    def test_follow_up_reception(self):
        """RX should depacketize a Follow_Up on the general port."""
        dut = self._make_general_dut()
        results = {}

        body = _build_ptp_body_timestamp(2000, 750_000_000)
        pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_FOLLOW_UP, body, seq_id=10)

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_GENERAL_PORT, dst_port=PTP_GENERAL_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["present"]   = (yield dut.rx.present)
            results["msg_type"]  = (yield dut.rx.msg_type)
            results["seq_id"]    = (yield dut.rx.seq_id)
            results["timestamp"] = (yield dut.rx.timestamp)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"],   1)
        self.assertEqual(results["msg_type"],  PTP_MSG_FOLLOW_UP)
        self.assertEqual(results["seq_id"],    10)
        self.assertEqual(results["timestamp"], _ts80(2000, 750_000_000))

    def test_announce_reception(self):
        """RX should accept Announce messages (different body, no timestamp extraction needed)."""
        dut = self._make_general_dut()
        results = {}

        # Announce has a 10-byte originTimestamp body (same as others).
        body = _build_ptp_body_timestamp(0, 0)
        pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_ANNOUNCE, body, seq_id=1)

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_GENERAL_PORT, dst_port=PTP_GENERAL_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["present"]  = (yield dut.rx.present)
            results["msg_type"] = (yield dut.rx.msg_type)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"],  1)
        self.assertEqual(results["msg_type"], PTP_MSG_ANNOUNCE)

    def test_invalid_version_rejected(self):
        """RX should reject packets with wrong PTP version."""
        dut = self._make_event_dut()
        results = {}

        body = _build_ptp_body_timestamp(0, 0)
        pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, body)
        pkt_data[1] = 0x04  # Corrupt version.

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                yield
            results["present"] = (yield dut.rx.present)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"], 0)

    def test_wrong_domain_rejected(self):
        """RX should reject packets with mismatched domain."""
        dut = self._make_event_dut()
        results = {}

        body = _build_ptp_body_timestamp(0, 0)
        pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, body, domain=5)

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                yield
            results["present"] = (yield dut.rx.present)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"], 0)

    def test_delay_resp_extraction(self):
        """RX should extract requestingPortIdentity from Delay_Resp."""
        dut = self._make_general_dut()
        results = {}

        body = _build_ptp_body_with_requester(200, 100_000_000, requester_id=SLAVE_CLOCK_ID)
        pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_DELAY_RESP, body, seq_id=7)

        def gen(dut):
            yield from _send_udp_packet(dut.udp_port, pkt_data, ip_address=ip,
                                        src_port=PTP_GENERAL_PORT, dst_port=PTP_GENERAL_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["present"]            = (yield dut.rx.present)
            results["msg_type"]           = (yield dut.rx.msg_type)
            results["timestamp"]          = (yield dut.rx.timestamp)
            results["requesting_port_id"] = (yield dut.rx.requesting_port_id)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["present"],  1)
        self.assertEqual(results["msg_type"], PTP_MSG_DELAY_RESP)
        self.assertEqual(results["timestamp"], _ts80(200, 100_000_000))
        self.assertEqual(results["requesting_port_id"], SLAVE_CLOCK_ID)

    def test_rx_timeout_fires(self):
        """RX timeout should fire and assert timeout_error on stuck packet."""
        dut = self._make_event_dut()
        results = {"timeout_seen": False}

        # Send 40 bytes of a Sync (no last) to get past the depacketizer and jam the RX FSM.
        body = _build_ptp_body_timestamp(0, 0)
        full_pkt, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, body)
        truncated = full_pkt[:40]

        def gen(dut):
            # Send truncated packet (no last bit).
            for i, b in enumerate(truncated):
                yield dut.udp_port.source.valid.eq(1)
                yield dut.udp_port.source.data.eq(b)
                yield dut.udp_port.source.last.eq(0)
                yield dut.udp_port.source.ip_address.eq(ip)
                yield dut.udp_port.source.src_port.eq(PTP_EVENT_PORT)
                yield dut.udp_port.source.dst_port.eq(PTP_EVENT_PORT)
                yield
                while not (yield dut.udp_port.source.ready):
                    yield
            yield dut.udp_port.source.valid.eq(0)
            yield

            # Wait and check for timeout_error.
            for _ in range(1500):
                if (yield dut.rx.timeout_error):
                    results["timeout_seen"] = True
                    break
                yield

        run_simulation(dut, gen(dut))

        self.assertTrue(results["timeout_seen"])

    def test_consecutive_packets(self):
        """RX should handle two back-to-back packets correctly."""
        dut = self._make_event_dut()
        results = {}

        body1 = _build_ptp_body_timestamp(10, 100_000_000)
        pkt1, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, body1, seq_id=1,
                                         flags=(1 << PTP_TWO_STEP_FLAG_BIT))
        body2 = _build_ptp_body_timestamp(20, 200_000_000)
        pkt2, _, _  = _build_ptp_packet(PTP_MSG_SYNC, body2, seq_id=2,
                                         flags=(1 << PTP_TWO_STEP_FLAG_BIT))

        def gen(dut):
            # First packet.
            yield from _send_udp_packet(dut.udp_port, pkt1, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["pkt1_present"] = (yield dut.rx.present)
            results["pkt1_seq"]     = (yield dut.rx.seq_id)
            results["pkt1_ts"]      = (yield dut.rx.timestamp)

            # Wait for present to deassert.
            for _ in range(10):
                yield

            # Second packet.
            yield from _send_udp_packet(dut.udp_port, pkt2, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                if (yield dut.rx.present):
                    break
                yield
            results["pkt2_present"] = (yield dut.rx.present)
            results["pkt2_seq"]     = (yield dut.rx.seq_id)
            results["pkt2_ts"]      = (yield dut.rx.timestamp)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["pkt1_present"], 1)
        self.assertEqual(results["pkt1_seq"], 1)
        self.assertEqual(results["pkt1_ts"], _ts80(10, 100_000_000))
        self.assertEqual(results["pkt2_present"], 1)
        self.assertEqual(results["pkt2_seq"], 2)
        self.assertEqual(results["pkt2_ts"], _ts80(20, 200_000_000))

# Test TX ------------------------------------------------------------------------------------------

class TestPTPTX(unittest.TestCase):
    """Verify PTP TX packetization."""

    def _capture_tx(self, msg_type, p2p_mode=0):
        """Helper: trigger TX and capture output bytes."""
        dut_cls = type("DUT", (LiteXModule,), {})
        dut = dut_cls()
        dut.tsu = LiteEthTSU(clk_freq=SYS_CLK_FREQ)
        dut.tx  = LiteEthPTPTX(dut.tsu)
        dut.submodules += [dut.tsu, dut.tx]

        captured = []
        results  = {}

        def gen(dut):
            yield dut.tx.msg_type.eq(msg_type)
            yield dut.tx.seq_id.eq(99)
            yield dut.tx.domain.eq(0)
            yield dut.tx.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.tx.ip_address.eq(MASTER_IP)
            yield dut.tx.src_port.eq(PTP_EVENT_PORT)
            yield dut.tx.dst_port.eq(PTP_EVENT_PORT)
            yield dut.tx.p2p_mode.eq(p2p_mode)
            yield

            yield dut.tx.start.eq(1)
            yield
            yield dut.tx.start.eq(0)

            for _ in range(200):
                yield dut.tx.source.ready.eq(1)
                yield
                valid = (yield dut.tx.source.valid)
                if valid:
                    data = (yield dut.tx.source.data)
                    last = (yield dut.tx.source.last)
                    captured.append(data)
                    if last:
                        break

            results["launch"] = (yield dut.tx.launch)

        run_simulation(dut, gen(dut))

        return captured, results

    def test_delay_req_packet(self):
        """TX should produce a valid 44-byte Delay_Req packet."""
        captured, results = self._capture_tx(PTP_MSG_DELAY_REQ)

        self.assertEqual(len(captured), 44)
        self.assertEqual(captured[0] & 0x0F, PTP_MSG_DELAY_REQ)
        self.assertEqual(captured[1] & 0x0F, PTP_VERSION)

    def test_pdelay_req_packet(self):
        """TX should produce a valid Pdelay_Req packet in P2P mode."""
        captured, results = self._capture_tx(PTP_MSG_PDELAY_REQ, p2p_mode=1)

        self.assertEqual(len(captured), 44)
        self.assertEqual(captured[0] & 0x0F, PTP_MSG_PDELAY_REQ)
        self.assertEqual(captured[1] & 0x0F, PTP_VERSION)

    def test_tx_seq_id(self):
        """TX should embed the configured sequence ID in the header."""
        captured, _ = self._capture_tx(PTP_MSG_DELAY_REQ)

        # Bytes 30-31: sequenceId (big-endian).
        seq_id = (captured[30] << 8) | captured[31]
        self.assertEqual(seq_id, 99)

    def test_tx_source_port_id(self):
        """TX should embed the clock identity in sourcePortIdentity."""
        captured, _ = self._capture_tx(PTP_MSG_DELAY_REQ)

        # Bytes 20-29: sourcePortIdentity (10 bytes, big-endian).
        spid = 0
        for i in range(10):
            spid = (spid << 8) | captured[20 + i]
        self.assertEqual(spid, SLAVE_CLOCK_ID)

# Test PTP Top-Level -------------------------------------------------------------------------------

class TestPTPTop(unittest.TestCase):
    """Integration tests for the full PTP top-level module."""

    def test_e2e_exchange_locks(self):
        """PTP should lock after receiving valid E2E exchanges."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)
            yield
            yield event_port.sink.ready.eq(1)
            yield

            for i in range(5):
                t1_sec, t1_ns = 1000 + i, 100_000_000
                t4_sec, t4_ns = t1_sec, t1_ns + 30_000
                yield from _run_e2e_exchange(event_port, general_port, dut,
                    seq=i, t1_sec=t1_sec, t1_ns=t1_ns, t4_sec=t4_sec, t4_ns=t4_ns)

            results["locked"]    = (yield dut.locked)
            results["master_ip"] = (yield dut.master_ip)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["locked"],    1)
        self.assertEqual(results["master_ip"], MASTER_IP)

    def test_timeout_when_no_sync(self):
        """PTP should timeout if no Sync is received."""
        event_port   = UDPPort()
        general_port = UDPPort()
        dut = LiteEthPTP(event_port, general_port, sys_clk_freq=1_000_000,
                         timeout=0.001, monitor_debug=None)
        results = {}

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield
            for _ in range(2000):
                yield
            results["locked"]  = (yield dut.locked)
            results["timeout"] = (yield dut.timeout)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["locked"],  0)
        self.assertEqual(results["timeout"], 1)

    def test_wrong_domain_does_not_lock(self):
        """PTP should not lock when exchanges use wrong domain."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)  # Expect domain 0.
            yield
            yield event_port.sink.ready.eq(1)
            yield

            # Send exchanges with domain=5 — should be rejected.
            two_step_flag = 1 << PTP_TWO_STEP_FLAG_BIT
            for i in range(3):
                sync_body = _build_ptp_body_timestamp(0, 0)
                sync_pkt, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, sync_body,
                    seq_id=i, flags=two_step_flag, domain=5)
                yield from _send_udp_packet(event_port, sync_pkt, ip_address=ip,
                    src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
                for _ in range(200):
                    yield

            results["locked"] = (yield dut.locked)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["locked"], 0)

    def test_wrong_requester_flagged(self):
        """Delay_Resp with wrong requestingPortIdentity should increment mismatch counter."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}
        WRONG_CLOCK_ID = 0xDEADBEEF00000001

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)
            yield
            yield event_port.sink.ready.eq(1)
            yield

            # Run exchange with wrong requester in Delay_Resp.
            yield from _run_e2e_exchange(event_port, general_port, dut,
                seq=0, t1_sec=1000, t1_ns=100_000_000,
                t4_sec=1000, t4_ns=100_030_000,
                dresp_requester=WRONG_CLOCK_ID)

            for _ in range(100):
                yield

            results["wrong_requester_count"] = (yield dut.wrong_requester_count)
            results["locked"]                = (yield dut.locked)

        run_simulation(dut, gen(dut))

        self.assertGreater(results["wrong_requester_count"], 0)
        self.assertEqual(results["locked"], 0)

    def test_servo_phase_decreases(self):
        """Servo phase error magnitude should decrease over multiple exchanges."""
        dut, event_port, general_port = _make_ptp_dut()
        phase_history = []

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)
            yield
            yield event_port.sink.ready.eq(1)
            yield

            for i in range(8):
                t1_sec, t1_ns = 1000 + i, 100_000_000
                t4_sec, t4_ns = t1_sec, t1_ns + 30_000
                yield from _run_e2e_exchange(event_port, general_port, dut,
                    seq=i, t1_sec=t1_sec, t1_ns=t1_ns, t4_sec=t4_sec, t4_ns=t4_ns)

                phase = (yield dut.servo.phase_error)
                # Convert from unsigned to signed 33-bit.
                if phase >= (1 << 32):
                    phase -= (1 << 33)
                phase_history.append(abs(phase))

        run_simulation(dut, gen(dut))

        # After initial coarse step, phase should trend downward.
        # Compare average of last 3 vs first 3 (skip exchange 0 which is coarse).
        if len(phase_history) >= 6:
            early = sum(phase_history[1:4]) / 3
            late  = sum(phase_history[-3:]) / 3
            self.assertLessEqual(late, early + 1000)  # Allow some tolerance.

    def test_master_ip_learned(self):
        """PTP should learn master IP from first Sync exchange."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}
        OTHER_MASTER_IP = 0xC0A80165  # 192.168.1.101

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)
            yield
            yield event_port.sink.ready.eq(1)
            yield

            # Send Sync from specific master.
            two_step_flag = 1 << PTP_TWO_STEP_FLAG_BIT
            sync_body = _build_ptp_body_timestamp(0, 0)
            sync_pkt = _build_ptp_header(PTP_MSG_SYNC, seq_id=0, flags=two_step_flag)
            sync_pkt += _build_ptp_body_timestamp(0, 0)
            yield from _send_udp_packet(event_port, sync_pkt, ip_address=OTHER_MASTER_IP,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                yield

            results["master_ip"] = (yield dut.master_ip)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["master_ip"], OTHER_MASTER_IP)

    def test_invalid_header_counted(self):
        """Invalid PTP headers should increment the invalid_header_count."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.domain.eq(0)
            yield

            # Send packet with bad version.
            body = _build_ptp_body_timestamp(0, 0)
            pkt_data, ip, _ = _build_ptp_packet(PTP_MSG_SYNC, body)
            pkt_data[1] = 0x07  # Bad version.
            yield from _send_udp_packet(event_port, pkt_data, ip_address=ip,
                                        src_port=PTP_EVENT_PORT, dst_port=PTP_EVENT_PORT)
            for _ in range(100):
                yield

            results["invalid_header_count"] = (yield dut.invalid_header_count)

        run_simulation(dut, gen(dut))

        self.assertGreater(results["invalid_header_count"], 0)

    def test_enable_gate(self):
        """PTP should not process exchanges when enable=0."""
        dut, event_port, general_port = _make_ptp_dut()
        results = {}

        def gen(dut):
            yield dut.clock_id.eq(SLAVE_CLOCK_ID)
            yield dut.p2p_mode.eq(0)
            yield dut.domain.eq(0)
            yield dut.enable.eq(0)  # Disable.
            yield
            yield event_port.sink.ready.eq(1)
            yield

            for i in range(3):
                t1_sec, t1_ns = 1000 + i, 100_000_000
                t4_sec, t4_ns = t1_sec, t1_ns + 30_000
                yield from _run_e2e_exchange(event_port, general_port, dut,
                    seq=i, t1_sec=t1_sec, t1_ns=t1_ns, t4_sec=t4_sec, t4_ns=t4_ns)

            results["locked"] = (yield dut.locked)

        run_simulation(dut, gen(dut))

        self.assertEqual(results["locked"], 0)


if __name__ == "__main__":
    unittest.main()
