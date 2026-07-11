#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr import lfsr_scrambler
import pmt

class qa_lfsr_scrambler(gr_unittest.TestCase):

    def setUp(self):
        self.scrambler = lfsr_scrambler()
        self.captured_output = []

    def tearDown(self):
        self.scrambler = None
        self.captured_output = []

    def _scramble_and_capture(self, meta, payload):
        self.captured_output = []

        original_pub = self.scrambler.message_port_pub

        def _capture(port, msg):
            self.captured_output.append((port, msg))

        self.scrambler.message_port_pub = _capture
        try:
            pdu = pmt.cons(meta, payload)
            self.scrambler.handle_msg(pdu)
        finally:
            self.scrambler.message_port_pub = original_pub

    def test_instance(self):
        instance = lfsr_scrambler()
        self.assertIsNotNone(instance)

    def test_001_default_parameters(self):
        self.assertEqual(self.scrambler.mask, 0xA9)
        self.assertEqual(self.scrambler.seed, 0xFF)
        self.assertEqual(self.scrambler.reg_length, 8)

    def test_002_custom_parameters(self):
        custom = lfsr_scrambler(mask=0xA9, seed=0xAB, register_length=8)
        self.assertEqual(custom.mask, 0xA9)
        self.assertEqual(custom.seed, 0xAB)
        self.assertEqual(custom.reg_length, 8)

    def test_003_known_sequence_first_40_bits(self):
        # CCSDS first 40 randomizer bits:
        # 11111111 00111001 10011110 01011010 01101000
        # Scrambling zero bytes should output the random sequence bytes.
        data = bytes([0x00, 0x00, 0x00, 0x00, 0x00])
        scrambled = bytes(self.scrambler.apply_scrambling(data))
        self.assertEqual(scrambled, bytes([0xFF, 0x39, 0x9E, 0x5A, 0x68]))

    def test_004_scrambling_is_deterministic(self):
        data = bytes([0x12, 0x34, 0x56, 0x78])
        out1 = bytes(self.scrambler.apply_scrambling(data))
        out2 = bytes(self.scrambler.apply_scrambling(data))
        self.assertEqual(out1, out2)

    def test_005_double_scramble_roundtrip(self):
        data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A])
        scrambled = bytes(self.scrambler.apply_scrambling(data))
        recovered = bytes(self.scrambler.apply_scrambling(scrambled))
        self.assertEqual(recovered, data)

    def test_006_pdu_metadata_preserved(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("id"), pmt.from_long(7))
        payload_bytes = bytes([0x01, 0x02, 0x03, 0x04])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._scramble_and_capture(meta, payload)
        self.assertEqual(len(self.captured_output), 1)

        out_port, out_msg = self.captured_output[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.eqv(pmt.car(out_msg), meta))

    def test_007_pdu_payload_length_preserved(self):
        payload_bytes = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

        self._scramble_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_data = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(len(out_data), len(payload_bytes))

    def test_008_zero_length_payload(self):
        payload = pmt.init_u8vector(0, [])
        self._scramble_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_data = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_data, b"")

    def test_009_non_u8vector_input_handling(self):
        bad_payload = pmt.intern("not-a-u8vector")
        self._scramble_and_capture(pmt.make_dict(), bad_payload)
        self.assertEqual(len(self.captured_output), 0)

    def test_010_invalid_register_length(self):
        invalid = lfsr_scrambler(register_length=7)
        with self.assertRaises(ValueError):
            invalid.apply_scrambling(bytes([0x00]))

    def test_011_randomizer_period_255_bits(self):
        # A maximal-length 8-bit CCSDS randomizer repeats every 255 bits.
        data = bytes([0x00] * 64)  # 512 bits
        scrambled = bytes(self.scrambler.apply_scrambling(data))

        bits = []
        for byte in scrambled:
            for bit_idx in range(7, -1, -1):
                bits.append((byte >> bit_idx) & 1)

        for i in range(255):
            self.assertEqual(bits[i], bits[i + 255])


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_scrambler)
