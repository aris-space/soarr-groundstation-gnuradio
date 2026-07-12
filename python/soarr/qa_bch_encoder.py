#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest

from gnuradio.soarr import bch_encoder

import pmt

class qa_bch_encoder(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.encoder = bch_encoder()
        self.captured_output = []

    def tearDown(self):
        self.tb = None
        self.captured_output = []

    def _encode_and_capture(self, meta, payload):
        """Run encode_bch directly and capture message_port_pub outputs."""
        self.captured_output = []

        original_pub = self.encoder.message_port_pub

        def _capture(port, msg):
            self.captured_output.append((port, msg))

        self.encoder.message_port_pub = _capture
        try:
            pdu = pmt.cons(meta, payload)
            self.encoder.encode_bch(pdu)
        finally:
            self.encoder.message_port_pub = original_pub

        # If the encoder emitted multiple PDUs (one per codeword), merge their
        # payload bodies into a single synthetic PDU so existing tests that
        # expect a single output continue to work. Preserve the metadata from
        # the first emitted PDU.
        if len(self.captured_output) > 1:
            merged_bytes = bytearray()
            first_port = self.captured_output[0][0]
            merged_meta = pmt.car(self.captured_output[0][1])
            for _, msg in self.captured_output:
                body = pmt.cdr(msg)
                merged_bytes.extend(pmt.u8vector_elements(body))

            merged_pdu = pmt.cons(merged_meta, pmt.init_u8vector(len(merged_bytes), list(merged_bytes)))
            self.captured_output = [(first_port, merged_pdu)]

    def test_instance(self):
        """Test that encoder can be instantiated."""
        instance = bch_encoder()
        self.assertIsNotNone(instance)

    def test_001_default_polynomial(self):
        """Test encoder initialization with default polynomial 0xC5."""
        instance = bch_encoder()
        self.assertEqual(instance.polynomial, 0xC5)

    def test_002_custom_polynomial(self):
        """Test encoder initialization with a custom (non-default, valid) polynomial."""
        custom_poly = 0x89
        instance = bch_encoder(polynomial=custom_poly)
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

        # Gets added by the bch encoder to the last codeword of an message
        meta = pmt.dict_add(meta, pmt.intern("filled"), pmt.from_bool(False))
        self.assertTrue(pmt.equal(out_meta, meta))

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

    def test_016_three_codewords_generation(self):
        """Test generation of exactly 3 codewords (21 bytes -> 168 bits -> 3x56)."""
        input_data = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
                            0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE,
                            0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 21 input bytes = 168 bits exactly (3 codewords * 56 bits)
        # 3 codewords * 8 bytes/codeword = 24 output bytes
        self.assertEqual(len(out_bytes), 24)

        # Verify each codeword's information field
        self.assertEqual(out_bytes[0:7], input_data[0:7])    # Codeword 1
        self.assertEqual(out_bytes[8:15], input_data[7:14])  # Codeword 2
        self.assertEqual(out_bytes[16:23], input_data[14:21])  # Codeword 3

    def test_017_four_codewords_with_fill(self):
        """Test generation of 4 codewords with fill bits."""
        # 25 bytes = 200 bits, need 8 fill bits to reach 208 bits (56*3 + 40)
        # Then fill to 224 bits (4 codewords) = 32 fill bits needed
        input_data = bytes(range(25))  # 0x00 to 0x18
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 4 codewords * 8 bytes/codeword = 32 output bytes
        self.assertEqual(len(out_bytes), 32)

        # Verify information bits are preserved
        self.assertEqual(out_bytes[0:7], input_data[0:7])
        self.assertEqual(out_bytes[8:15], input_data[7:14])
        self.assertEqual(out_bytes[16:23], input_data[14:21])
        self.assertEqual(out_bytes[24:25], input_data[21:22])

    def test_018_large_payload_seven_codewords(self):
        """Test encoding of large payload spanning 7 codewords."""
        # 49 bytes = 392 bits = 7 * 56 bits exactly
        input_data = bytes([i % 256 for i in range(49)])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 7 codewords * 8 bytes/codeword = 56 output bytes
        self.assertEqual(len(out_bytes), 56)

        # Verify all input bytes are preserved in information fields
        for cw_idx in range(7):
            info_start = cw_idx * 8
            info_end = info_start + 7
            expected_start = cw_idx * 7
            expected_end = expected_start + 7
            self.assertEqual(out_bytes[info_start:info_end], input_data[expected_start:expected_end])

    def test_019_exact_boundary_7_bytes(self):
        """Test that exactly 7 bytes (56 bits) produces exactly 1 codeword."""
        input_data = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(len(out_bytes), 8)
        self.assertEqual(out_bytes[:7], input_data)

    def test_020_exact_boundary_14_bytes(self):
        """Test that exactly 14 bytes (112 bits) produces exactly 2 codewords."""
        input_data = bytes([i for i in range(14)])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(len(out_bytes), 16)
        self.assertEqual(out_bytes[:7], input_data[:7])
        self.assertEqual(out_bytes[8:15], input_data[7:14])

    def test_021_exact_boundary_21_bytes(self):
        """Test that exactly 21 bytes (168 bits) produces exactly 3 codewords."""
        input_data = bytes([i for i in range(21)])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(len(out_bytes), 24)
        self.assertEqual(out_bytes[:7], input_data[:7])
        self.assertEqual(out_bytes[8:15], input_data[7:14])
        self.assertEqual(out_bytes[16:23], input_data[14:21])

    def test_022_fill_bits_across_multiple_codewords(self):
        """Test fill bit pattern (0x55) applied across multiple codewords."""
        # 8 bytes = 64 bits, need 48 fill bits -> 2 codewords total
        input_data = bytes([0xAA] * 8)
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 16 output bytes (2 codewords)
        self.assertEqual(len(out_bytes), 16)

        # First codeword: 7 bytes info (preserved) + 1 parity byte
        self.assertEqual(out_bytes[0:7], input_data[:7])
        # Parity byte should have filler bit (LSB) = 0
        self.assertEqual(out_bytes[7] & 0x01, 0)

        # Second codeword: 1 byte info + fill bits (0x55 pattern) + 1 parity byte
        self.assertEqual(out_bytes[8], input_data[7])
        self.assertEqual(out_bytes[9:15], bytes([0x55] * 6))
        # Second parity byte should have filler bit (LSB) = 0
        self.assertEqual(out_bytes[15] & 0x01, 0)

    def test_023_each_codeword_has_filler_bit(self):
        """Test that each codeword has filler bit (LSB of parity byte = 0)."""
        input_data = bytes([0xFF] * 15)
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 15 bytes = 120 bits, need 48 fill bits -> 3 codewords (24 output bytes)
        self.assertEqual(len(out_bytes), 24)

        # Check filler bit (LSB) in each parity byte
        parity_byte_1 = out_bytes[7]
        parity_byte_2 = out_bytes[15]
        parity_byte_3 = out_bytes[23]

        self.assertEqual(parity_byte_1 & 0x01, 0, "Codeword 1 filler bit should be 0")
        self.assertEqual(parity_byte_2 & 0x01, 0, "Codeword 2 filler bit should be 0")
        self.assertEqual(parity_byte_3 & 0x01, 0, "Codeword 3 filler bit should be 0")

    def test_024_large_payload_50_bytes(self):
        """Test large payload of 50 bytes across 7+ codewords."""
        input_data = bytes([i % 256 for i in range(50)])
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 50 bytes = 400 bits, need 8 fill bits -> 408 bits = 7.28... -> 8 codewords = 64 bytes
        self.assertEqual(len(out_bytes), 64)

        # Verify information bits from first few codewords
        self.assertEqual(out_bytes[:7], input_data[:7])
        self.assertEqual(out_bytes[8:15], input_data[7:14])
        self.assertEqual(out_bytes[16:23], input_data[14:21])

    def test_025_random_data_multiple_codewords(self):
        """Test encoding of random pattern across multiple codewords."""
        input_data = bytes([0x47, 0x6F, 0x74, 0x79, 0x61, 0x0A, 0x0B,  # Codeword 1
                            0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12,  # Codeword 2
                            0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19])  # Codeword 3
        payload = pmt.init_u8vector(len(input_data), list(input_data))

        self._encode_and_capture(pmt.make_dict(), payload)
        self.assertEqual(len(self.captured_output), 1)

        _, out_msg = self.captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 21 bytes = 3 codewords = 24 output bytes
        self.assertEqual(len(out_bytes), 24)

        # Verify information fields preserved
        for cw in range(3):
            info_idx = cw * 8
            data_idx = cw * 7
            self.assertEqual(out_bytes[info_idx:info_idx + 7], input_data[data_idx:data_idx + 7])

    # Additional: polynomial is rejected at construction time - must be
    # an 8-bit value with bit 7 set (a valid degree-7 generator polynomial),
    # matching every other reviewed block's fail-fast-at-construction precedent.
    def test_026_invalid_polynomial_raises_at_construction(self):
        with self.assertRaises(ValueError):
            bch_encoder(polynomial=0x00)
        with self.assertRaises(ValueError):
            bch_encoder(polynomial=0x7F)
        with self.assertRaises(ValueError):
            bch_encoder(polynomial=0x100)
        with self.assertRaises(ValueError):
            bch_encoder(polynomial=-1)

    # Additional: a non-pair input must not crash the handler - dropped
    # cleanly (logged, no publish) instead of pmt.car raising out of it.
    def test_027_non_pair_input_is_dropped_not_raised(self):
        captured = []
        original_pub = self.encoder.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        self.encoder.message_port_pub = _capture
        try:
            self.encoder.encode_bch(pmt.intern("not-a-pair"))  # must not raise
        finally:
            self.encoder.message_port_pub = original_pub

        self.assertEqual(len(captured), 0)

    # Additional: an empty payload is dropped cleanly, still covered now
    # that the check moved inside the catch-log-drop try.
    def test_028_empty_payload_is_dropped_not_raised(self):
        meta = pmt.make_dict()
        payload = pmt.init_u8vector(0, [])
        msg = pmt.cons(meta, payload)

        original_pub = self.encoder.message_port_pub
        captured = []

        def _capture(port, out_msg):
            captured.append((port, out_msg))

        self.encoder.message_port_pub = _capture
        try:
            self.encoder.encode_bch(msg)  # must not raise
        finally:
            self.encoder.message_port_pub = original_pub

        self.assertEqual(len(captured), 0)

    # Additional: a failure past field extraction (e.g. publish itself
    # raising) is caught, logged, and dropped - not left to raise out of
    # the real message handler.
    def test_029_internal_publish_failure_is_dropped_not_raised(self):
        meta = pmt.make_dict()
        payload = pmt.init_u8vector(7, [0] * 7)
        msg = pmt.cons(meta, payload)

        def _raise(port, out_msg):
            raise RuntimeError("simulated publish failure")

        original_pub = self.encoder.message_port_pub
        self.encoder.message_port_pub = _raise
        try:
            self.encoder.encode_bch(msg)  # must not raise
        finally:
            self.encoder.message_port_pub = original_pub


if __name__ == '__main__':
    gr_unittest.run(qa_bch_encoder)
