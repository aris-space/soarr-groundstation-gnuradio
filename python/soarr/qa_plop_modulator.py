#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time

import numpy as np
from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import plop_modulator

SPS = 8  # small for fast tests; the block works the same at 40
CLTU = bytes.fromhex("eb90") + bytes(range(16)) + bytes.fromhex("c5c5c5c5c5c5c579")
ACQ = 64  # default acquisition_length


def pdu(data):
    return pmt.cons(pmt.make_dict(), pmt.init_u8vector(len(data), list(data)))


class qa_plop_modulator(gr_unittest.TestCase):

    def _run(self, block, actions, settle=0.3):
        """Run block -> throttle -> vector_sink; each action is (delay_s,
        callable). The throttle stands in for the SDR's sample clock, which
        paces the continuous PLOP-2 carrier in a real flowgraph."""
        tb = gr.top_block()
        clock = blocks.throttle(gr.sizeof_gr_complex, 200e3, True)
        sink = blocks.vector_sink_c()
        tb.connect(block, clock, sink)
        tb.start()
        for delay, action in actions:
            time.sleep(delay)
            action()
        time.sleep(settle)
        tb.stop()
        tb.wait()
        return np.array(sink.data()), sink.tags()

    def _post(self, block, port, msg):
        return lambda: block.to_basic_block()._post(pmt.intern(port), msg)

    def _demod_bytes(self, block, samples):
        """Ideal receiver: matched filter, sample at the symbol centres,
        differential decoding, bits -> bytes (MSB first)."""
        taps = block.taps
        mf = np.convolve(samples, taps[::-1])
        delay = len(taps) - 1  # transmit + matched filter group delay
        symbols = mf[delay::block.samples_per_symbol].real
        bits = (symbols > 0).astype(np.uint8)
        if block.differential:
            bits = bits ^ np.concatenate(([0], bits[:-1]))
        usable = len(bits) // 8 * 8
        return np.packbits(bits[:usable]).tobytes()

    def _tags(self, tags, key):
        return [t.offset for t in tags if pmt.symbol_to_string(t.key) == key]

    def test_instance(self):
        self.assertIsNotNone(plop_modulator())

    def test_001_plop1_bursts_with_sob_eob_and_nothing_between(self):
        mod = plop_modulator(mode=1, samples_per_symbol=SPS)
        samples, tags = self._run(mod, [(0.05, self._post(mod, "in", pdu(CLTU))),
                                        (0.2, self._post(mod, "in", pdu(CLTU)))])
        sob, eob = self._tags(tags, "tx_sob"), self._tags(tags, "tx_eob")
        self.assertEqual(len(sob), 2)
        self.assertEqual(len(eob), 2)
        burst_symbols = (ACQ + len(CLTU) + 4) * 8
        burst_len = burst_symbols * SPS + mod.flush_length
        # Two bursts back to back in the stream: no samples between them
        self.assertEqual(len(samples), 2 * burst_len)
        self.assertEqual(sob, [0, burst_len])
        self.assertEqual(eob, [burst_len - 1, 2 * burst_len - 1])

    def test_002_plop1_burst_demodulates_to_acquisition_cltu_tail(self):
        mod = plop_modulator(mode=1, samples_per_symbol=SPS)
        samples, _ = self._run(mod, [(0.05, self._post(mod, "in", pdu(CLTU)))])
        data = self._demod_bytes(mod, samples)
        fill = bytes([0xFF]) if mod.differential else bytes([0xAA])
        self.assertEqual(data[:ACQ + len(CLTU) + 4], fill * ACQ + CLTU + fill * 4)

    def test_003_plop2_continuous_carrier_carries_cltu(self):
        mod = plop_modulator(mode=2, samples_per_symbol=SPS)
        samples, tags = self._run(mod, [(0.1, self._post(mod, "in", pdu(CLTU)))])
        self.assertEqual(self._tags(tags, "tx_sob"), [0])
        self.assertEqual(self._tags(tags, "tx_eob"), [])
        data = self._demod_bytes(mod, samples)
        fill = bytes([0xFF]) if mod.differential else bytes([0xAA])
        self.assertTrue(data.startswith(fill * ACQ))  # acquisition at carrier start
        start = data.find(CLTU)
        self.assertGreater(start, ACQ)                # CLTU inserted into the idle stream
        self.assertEqual(data[start - 4:start], fill * 4)
        self.assertEqual(data[start + len(CLTU):start + len(CLTU) + 4], fill * 4)

    def test_004_switch_plop2_to_plop1_ends_carrier(self):
        mod = plop_modulator(mode=2, samples_per_symbol=SPS)
        samples, tags = self._run(mod, [(0.1, self._post(mod, "mode", pmt.from_long(1)))], settle=0.3)
        eob = self._tags(tags, "tx_eob")
        self.assertEqual(len(eob), 1)
        self.assertEqual(eob[0], len(samples) - 1)  # nothing after the carrier ended

    def test_005_switch_plop1_to_plop2_starts_carrier(self):
        mod = plop_modulator(mode=1, samples_per_symbol=SPS)
        samples, tags = self._run(mod, [(0.1, self._post(mod, "mode", pmt.from_long(2)))], settle=0.2)
        self.assertEqual(self._tags(tags, "tx_sob"), [0])
        self.assertGreater(len(samples), 0)
        data = self._demod_bytes(mod, samples)
        fill = bytes([0xFF]) if mod.differential else bytes([0xAA])
        self.assertTrue(data.startswith(fill * ACQ))

    def test_006_set_mode_setter_matches_message_port(self):
        mod = plop_modulator(mode=1, samples_per_symbol=SPS)
        samples, tags = self._run(mod, [(0.1, lambda: mod.set_mode(2))], settle=0.2)
        self.assertEqual(self._tags(tags, "tx_sob"), [0])

    def test_007_invalid_parameters_raise(self):
        for kwargs in ({"mode": 3}, {"samples_per_symbol": 1}, {"excess_bw": 0},
                       {"acquisition_length": -1}, {"tail_length": -1}, {"fill_byte": 256}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    plop_modulator(**kwargs)

    def test_008_invalid_messages_dropped_not_raised(self):
        mod = plop_modulator(mode=1, samples_per_symbol=SPS)
        mod.handle_cltu(pmt.intern("not-a-pair"))
        mod.handle_cltu(pdu(b""))
        mod.handle_mode(pmt.from_long(7))
        mod.handle_mode(pmt.intern("x"))
        self.assertEqual(mod.mode, 1)


if __name__ == '__main__':
    gr_unittest.run(qa_plop_modulator)
