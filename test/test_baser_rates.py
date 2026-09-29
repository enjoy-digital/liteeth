#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest

from migen import Record, Signal
from liteeth.phy.serial.baser.pcs.ber_mon import PCSRXBERMonitor
from liteeth.phy.serial.baser.pcs.watchdog import PCSRXWatchdog
from liteeth.phy.serial.baser.pcs.common import SYNC_CTRL
from liteeth.phy.serial.baser.wrappers.usp_gt import USP_GTY_10G_BASER, USP_GTY_25G_BASER
from test.test_pcs_baser import run_cycles


class TestBASERRates(unittest.TestCase):
    def test_ber_threshold_and_recovery(self):
        for threshold in [16, 97]:
            for invalid in [threshold - 1, threshold, threshold + 1]:
                with self.subTest(threshold=threshold, invalid=invalid):
                    # Accelerate the timer while retaining the full error threshold.
                    dut = PCSRXBERMonitor(threshold=threshold, count_window=256)
                    samples = run_cycles(dut, {
                        dut.block_lock    : [1],
                        dut.serdes_rx_hdr : [0]*invalid + [SYNC_CTRL]*768,
                    }, {"high_ber" : dut.rx_high_ber})
                    self.assertEqual(any(s['high_ber'] for s in samples), invalid >= threshold)
                    self.assertFalse(samples[-1]['high_ber'])

    def test_rate_parameters(self):
        for cls, threshold, count in [(USP_GTY_10G_BASER, 16, 19531),
                                     (USP_GTY_25G_BASER, 97, 781250)]:
            with self.subTest(phy=cls.__name__):
                pads = Record([('txp', 1), ('txn', 1), ('rxp', 1), ('rxn', 1)])
                phy = cls(Signal(), pads, 100e6, with_csr=False)
                monitor = phy.pcs.rx.interface.ber_mon
                self.assertEqual(monitor.threshold, threshold)
                self.assertEqual(monitor.count_window, count)

    def test_watchdog_fault_at_window_boundary(self):
        for fault in ['rx_block_lock', 'rx_high_ber']:
            for offset in range(5):
                with self.subTest(fault=fault, offset=offset):
                    dut = PCSRXWatchdog(count_125us=4)
                    moment = 16*5 + offset
                    lock = [1]*moment + [0 if fault == 'rx_block_lock' else 1]*5 + [1]*100
                    ber  = [0]*moment + [1 if fault == 'rx_high_ber' else 0]*5 + [0]*100
                    samples = run_cycles(dut, {
                        dut.serdes_rx_hdr : [SYNC_CTRL]*len(lock),
                        dut.rx_block_lock : lock,
                        dut.rx_high_ber   : ber,
                    }, {'status' : dut.rx_status})
                    self.assertFalse(any(s['status'] for s in samples[moment + 1:moment + 6]))
                    self.assertTrue(samples[-1]['status'])

    def test_pcs_loss_reset_and_ordered_sets(self):
        from migen import Module, ClockDomainsRenamer, ResetInserter, Mux
        from liteeth.phy.serial.baser.pcs import PCS
        from test.test_pcs_baser import xgmii_stimulus, LOCAL_FAULT_DATA, LOCAL_FAULT_CTRL
        for pipelined in [False, True]:
            with self.subTest(pipelined=pipelined):
                dut = Module()
                dut.submodules.pcs = pcs = ResetInserter()(ClockDomainsRenamer({
                    'eth_tx': 'sys', 'eth_rx': 'sys'})(PCS(count_125us=8,
                    with_pipelining=pipelined, ber_threshold=97, ber_count=256)))
                corrupt = Signal()
                dut.comb += [
                    pcs.serdes_rx_data.eq(pcs.serdes_tx_data),
                    pcs.serdes_rx_hdr.eq(Mux(corrupt, 0, pcs.serdes_tx_hdr)),
                ]
                transfers = xgmii_stimulus(1200)
                # Remote Fault ordered sets must survive encoding, scrambling and reception.
                remote = (0x0200009c0200009c, 0x11)
                transfers[1100:1200] = [remote]*100
                samples = run_cycles(dut, {
                    pcs.xgmii_txd : [data for data, _ in transfers],
                    pcs.xgmii_txc : [ctrl for _, ctrl in transfers],
                    corrupt       : [0]*350 + [1]*80 + [0]*770,
                    pcs.reset     : [0]*750 + [1]*4 + [0]*446,
                }, {'status': pcs.rx_status, 'lock': pcs.rx_block_lock,
                    'data': pcs.xgmii_rxd, 'ctrl': pcs.xgmii_rxc})
                self.assertTrue(samples[340]['status'])
                self.assertFalse(samples[420]['lock'])
                self.assertFalse(samples[420]['status'])
                self.assertEqual((samples[420]['data'], samples[420]['ctrl']),
                    (LOCAL_FAULT_DATA, LOCAL_FAULT_CTRL))
                self.assertTrue(samples[740]['status'])
                self.assertFalse(samples[754]['status'])
                self.assertTrue(samples[-1]['status'])
                self.assertEqual((samples[-1]['data'], samples[-1]['ctrl']), remote)
