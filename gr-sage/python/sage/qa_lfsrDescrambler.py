#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.sage import lfsrDescrambler
import pmt

class qa_lfsrDescrambler(gr_unittest.TestCase):

    def setUp(self):
        self.descrambler = lfsrDescrambler()
        self.captured_output = []

    def tearDown(self):
        self.descrambler = None
        self.captured_output = []

    def test_instance(self):
        instance = lfsrDescrambler()
        self.assertIsNotNone(instance)

    def _descramble_and_capture(self, meta, payload):
        self.captured_output = []

        original_pub = self.descrambler.message_port_pub

        def _capture(port, msg):
            self.captured_output.append((port, msg))

        self.descrambler.message_port_pub = _capture
        try:
            pdu = pmt.cons(meta, payload)
            self.descrambler.descramble_msg(pdu)
        finally:
            self.descrambler.message_port_pub = original_pub

    def test_001_default_parameters(self):
        self.assertEqual(self.descrambler.mask, 0xA9)
        self.assertEqual(self.descrambler.seed, 0xFF)
        self.assertEqual(self.descrambler.register_length, 8)

    def test_002_custom_parameters(self):
        custom = lfsrDescrambler(mask=0xA9, seed=0xAB, register_length=8)
        self.assertEqual(custom.mask, 0xA9)
        self.assertEqual(custom.seed, 0xAB)
        self.assertEqual(custom.register_length, 8)

    def test_003_known_sequence_descramble(self):
        scrambled = bytes([0xFF, 0x39, 0x9E, 0x5A, 0x68])
        recovered = bytes(self.descrambler.apply_descrambling(scrambled))
        self.assertEqual(recovered, bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

    def test_004_pdu_metadata_preserved(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("id"), pmt.from_long(7))
        payload_bytes = bytes([0xFF, 0x39, 0x9E, 0x5A, 0x68])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._descramble_and_capture(meta, payload)
        self.assertEqual(len(self.captured_output), 1)

        out_port, out_msg = self.captured_output[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.eqv(pmt.car(out_msg), meta))

    def test_005_pdu_payload_length_preserved(self):
        payload_bytes = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._descramble_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_data = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(len(out_data), len(payload_bytes))

    def test_006_zero_length_payload(self):
        payload = pmt.init_u8vector(0, [])
        self._descramble_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_data = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_data, b"")

    def test_007_non_u8vector_input_handling(self):
        bad_payload = pmt.intern("not-a-u8vector")
        self._descramble_and_capture(pmt.make_dict(), bad_payload)
        self.assertEqual(len(self.captured_output), 0)

    def test_008_invalid_register_length(self):
        # register_length must be 8 for CCSDS randomizer, so we expect errors for other values
        with self.assertRaises(ValueError):
            invalid = lfsrDescrambler(register_length=7)


if __name__ == '__main__':
    gr_unittest.run(qa_lfsrDescrambler)
