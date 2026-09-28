#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr import lfsr_scrambler, lfsr_descrambler
import pmt


class qa_lfsr_round_trip(gr_unittest.TestCase):

    def setUp(self):
        self.scrambler = lfsr_scrambler()
        self.descrambler = lfsr_descrambler()
        self.captured_output = []

    def tearDown(self):
        self.scrambler = None
        self.descrambler = None
        self.captured_output = []

    def _connect_blocks(self):
        original_scrambler_pub = self.scrambler.message_port_pub
        original_descrambler_pub = self.descrambler.message_port_pub

        def _scrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                self.descrambler.descramble_msg(msg)

        def _descrambler_pub(port, msg):
            self.captured_output.append((port, msg))

        self.scrambler.message_port_pub = _scrambler_pub
        self.descrambler.message_port_pub = _descrambler_pub

        return original_scrambler_pub, original_descrambler_pub

    def _restore_blocks(self, pubs):
        self.scrambler.message_port_pub = pubs[0]
        self.descrambler.message_port_pub = pubs[1]

    def _run_roundtrip(self, meta, payload):
        self.captured_output = []
        pubs = self._connect_blocks()
        try:
            pdu = pmt.cons(meta, payload)
            self.scrambler.handle_msg(pdu)
        finally:
            self._restore_blocks(pubs)

    def test_instance(self):
        instance = lfsr_scrambler()
        self.assertIsNotNone(instance)

    def test_001_roundtrip_recovers_payload(self):
        payload_bytes = bytes([0x12, 0x34, 0x56, 0x78, 0x9A])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._run_roundtrip(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_bytes, payload_bytes)

    def test_002_roundtrip_preserves_metadata(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("id"), pmt.from_long(7))
        payload_bytes = bytes([0xFF, 0x39, 0x9E, 0x5A, 0x68])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._run_roundtrip(meta, payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        self.assertTrue(pmt.eqv(pmt.car(out_msg), meta))
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_bytes, payload_bytes)


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_round_trip)