#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest

from gnuradio.sage import bchEncoder

import pmt

class qa_bchEncoder(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.encoder = bchEncoder()
        self.captured_output = []

    def tearDown(self):
        self.tb = None
        self.captured_output = []

    def _encode_and_capture(self, meta, payload):
        """Run encodeBCH directly and capture message_port_pub outputs."""
        self.captured_output = []

        original_pub = self.encoder.message_port_pub

        def _capture(port, msg):
            self.captured_output.append((port, msg))

        self.encoder.message_port_pub = _capture
        try:
            pdu = pmt.cons(meta, payload)
            self.encoder.encodeBCH(pdu)
        finally:
            self.encoder.message_port_pub = original_pub

    def test_instance(self):
        """Test that encoder can be instantiated."""
        instance = bchEncoder()
        self.assertIsNotNone(instance)

    def test_001_default_polynomial(self):
        """Test encoder initialization with default polynomial 0xC5."""
        instance = bchEncoder()
        self.assertEqual(instance.polynomial, 0xC5)

    def test_002_custom_polynomial(self):
        """Test encoder initialization with custom polynomial."""
        custom_poly = 0xC5
        instance = bchEncoder(polynomial=custom_poly)
        self.assertEqual(instance.polynomial, custom_poly)

    def test_003_all_zeros_input(self):
        """Test encoding of all-zero 56-bit input."""
        input_data = bytes([0x00] * 7)
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(len(out_bytes), 8)

    def test_004_all_ones_input(self):
        """Test encoding of all-one 56-bit input."""
        input_data = bytes([0xFF] * 7)
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(len(out_bytes), 8)

    def test_005_alternating_pattern(self):
        """Test encoding with alternating bit pattern."""
        input_data = bytes([0xAA] * 7)
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

    def test_006_output_size(self):
        """Test that output PDU contains 8 bytes total."""
        input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(len(out_bytes), 8)

    def test_007_information_bits_preserved(self):
        """Test that information bits are preserved in output (systematic code)."""
        input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_bytes[:7], input_data)

    def test_008_parity_bits_are_complemented(self):
        """Test that parity bits are binary and packed with zero filler bit."""
        input_data = bytes([0xFF] * 7)
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(pmt.make_dict(), payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        parity_plus_filler = out_bytes[7]
        self.assertEqual(parity_plus_filler & 0x01, 0)

    def test_009_different_input_patterns(self):
        """Test encoding with various input patterns."""
        test_patterns = [
            bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),  # All zeros
            bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),  # All ones
            bytes([0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA]),  # Alternating
            bytes([0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40]),  # Single bits
            bytes([0xFE, 0xFD, 0xFB, 0xF7, 0xEF, 0xDF, 0xBF]),  # Single-zero bits
        ]
        for pattern in test_patterns:
            payload = pmt.init_u8vector(len(pattern), list(pattern))
            self._encode_and_capture(pmt.make_dict(), payload)
            self.assertEqual(len(self.captured_output), 1)
            _, out_msg = self.captured_output[0]
            out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
            self.assertEqual(len(out_bytes), 8)
            self.assertEqual(out_bytes[:7], pattern)

    def test_010_linearity_property(self):
        """Test affine property induced by complemented parity bits."""
        pattern1 = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        parity1 = self.encoder._compute_parity_bits(pattern1)

        pattern2 = bytes([0xFE, 0xCB, 0xA9, 0x87, 0x65, 0x43, 0x21])
        parity2 = self.encoder._compute_parity_bits(pattern2)

        xor_pattern = bytes([pattern1[i] ^ pattern2[i] for i in range(7)])
        parity_xor = self.encoder._compute_parity_bits(xor_pattern)

        # Because the encoder returns complemented parity bits, the mapping is affine:
        # c(a xor b) = c(a) xor c(b) xor 0x7F  (bitwise over 7 parity bits)
        expected_affine = tuple((parity1[i] ^ parity2[i] ^ 1) for i in range(7))
        self.assertEqual(tuple(parity_xor), expected_affine)

    def test_011_single_bit_flip_detection(self):
        """Test that single-bit input change alters parity bits."""
        original = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        parity_original = self.encoder._compute_parity_bits(original)

        flipped = bytearray(original)
        flipped[0] ^= 0x01
        flipped = bytes(flipped)
        parity_flipped = self.encoder._compute_parity_bits(flipped)

        self.assertNotEqual(tuple(parity_original), tuple(parity_flipped))

    def test_012_pdu_metadata_preserved(self):
        """Test that PDU metadata is preserved through encoding."""
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("test_key"), pmt.intern("test_value"))

        input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        payload = pmt.init_u8vector(len(input_data), list(input_data))
        self._encode_and_capture(meta, payload)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.eqv(out_meta, meta))

    def test_013_fill_bits_for_non_integral_payload(self):
        """Test CCSDS fill pattern for payloads that do not align to 56 bits."""
        input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 6 input bytes -> 48 bits, so 8 fill bits are needed: 01010101 = 0x55
        self.assertEqual(len(out_bytes), 8)
        self.assertEqual(out_bytes[:6], input_data)
        self.assertEqual(out_bytes[6], 0x55)

    def test_014_multiple_codewords_with_fill(self):
        """Test stuffing and encoding when payload spans multiple codewords."""
        input_data = bytes([0x01, 0x02, 0x03, 0x04, 0x05,
                            0x06, 0x07, 0x08, 0x09, 0x0A])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 10 input bytes -> 80 bits, add 32 fill bits -> 2 codewords -> 16 output bytes.
        self.assertEqual(len(out_bytes), 16)

        # First codeword information field is first 7 bytes.
        self.assertEqual(out_bytes[:7], input_data[:7])

        # Second codeword information field: remaining 3 bytes + 4 fill bytes (0x55 pattern).
        self.assertEqual(out_bytes[8:11], input_data[7:10])
        self.assertEqual(out_bytes[11:15], bytes([0x55, 0x55, 0x55, 0x55]))

    def test_015_non_u8vector_input_handling(self):
        """Test that encoder ignores non-u8vector input without raising."""
        bad_payload = pmt.intern("not-a-u8vector")
        self._encode_and_capture(pmt.make_dict(), bad_payload)
        self.assertEqual(len(self.captured_output), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_bchEncoder)
