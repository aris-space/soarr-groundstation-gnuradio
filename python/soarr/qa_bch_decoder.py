#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import pmt
from gnuradio.soarr import bch_decoder, bch_encoder

class qa_bch_decoder(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def test_instance(self):
        instance = bch_decoder()
        self.assertIsNotNone(instance)
        self.assertEqual(instance.mode, 0)
        self.assertEqual(instance.generator_polynomial, 0xC5)
        self.assertEqual(instance.primitive_polynomial, 0x43)

    def _run_and_capture(self, dut, pdu):
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            dut.error_correction_mode(pdu)
        finally:
            dut.message_port_pub = original_pub

        return captured

    def _encode_payload(self, encoder, payload):
        """Helper to encode a payload and capture the output."""
        captured = []
        original_pub = encoder.message_port_pub
        
        def _capture(port, msg):
            captured.append((port, msg))
        
        encoder.message_port_pub = _capture
        try:
            meta = pmt.make_dict()
            payload_u8 = pmt.init_u8vector(len(payload), payload)
            pdu = pmt.cons(meta, payload_u8)
            encoder.encode_bch(pdu)
        finally:
            encoder.message_port_pub = original_pub
        
        if captured:
            return captured[0][1]
        return None

    def test_001_error_correction_mode_exists(self):
        instance = bch_decoder()
        self.assertIsNotNone(instance.error_correction_mode)
        self.assertTrue(callable(instance.error_correction_mode))

    def test_002_invalid_polynomial(self):
        # Test invalid generator polynomial values (must be between 0x00 and 0xFF)
        with self.assertRaises(ValueError):
            bch_decoder(generator_polynomial=-1)
        with self.assertRaises(ValueError):
            bch_decoder(generator_polynomial=0x100)
        
        # Test invalid primitive polynomial values (must be between 0x00 and 0xFF)
        with self.assertRaises(ValueError):
            bch_decoder(primitive_polynomial=-1)
        with self.assertRaises(ValueError):
            bch_decoder(primitive_polynomial=0x100)

    def test_003_invalid_pdu_structure_no_pair(self):
        instance = bch_decoder()
        # Not a pair
        no_pair = pmt.PMT_NIL
        with self.assertRaises(ValueError):
            instance.error_correction_mode(no_pair)

    def test_004_invalid_pdu_structure_no_u8vector(self):
        instance = bch_decoder()
        # Pair without u8vector payload
        pair_no_data = pmt.cons(pmt.make_dict(), pmt.PMT_NIL)
        with self.assertRaises(ValueError):
            instance.error_correction_mode(pair_no_data)

    def test_006_invalid_pdu_structure_wrong_type(self):
        instance = bch_decoder()
        # Pair with wrong payload type
        pair_no_u8 = pmt.cons(pmt.make_dict(), pmt.init_u16vector(4, [0x00] * 4))
        with self.assertRaises(ValueError):
            instance.error_correction_mode(pair_no_u8)
  
    def test_007_invalid_data_length_too_short(self):
        instance = bch_decoder()
        # Not enough data (less than 64 bits/8 bytes)
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(7, [0x00] * 7))
        with self.assertRaises(ValueError):
            instance.error_correction_mode(msg)

    def test_008_invalid_data_length_too_long(self):
        instance = bch_decoder()
        # Too much data (more than 64 bits/8 bytes)
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(9, [0x00] * 9))
        with self.assertRaises(ValueError):
            instance.error_correction_mode(msg)

    def test_009_valid_data_length(self):
        instance = bch_decoder()
        # Valid data amount (8 bytes = 64 bits) should not raise
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, [0x00] * 8))
        instance.error_correction_mode(msg)  # Should not raise

    def test_010_no_error_valid_codeword(self):
        """Test that valid encoded codewords pass through unchanged."""
        encoder = bch_encoder()
        decoder = bch_decoder()
        
        # Use encoder to generate valid codeword
        payload = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        encoded_pdu = self._encode_payload(encoder, payload)
        valid_codeword = list(pmt.u8vector_elements(pmt.cdr(encoded_pdu)))
        
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, valid_codeword))
        captured = self._run_and_capture(decoder, msg)
        
        self.assertEqual(len(captured), 1)
        _, out_msg = captured[0]
        corrected_data = list(pmt.u8vector_elements(pmt.cdr(out_msg)))
        
        # Should have no error flag
        self.assertFalse(pmt.dict_has_key(pmt.car(out_msg), pmt.intern("bch_error")))
        # Data should match original payload
        self.assertEqual(payload, corrected_data)

    def test_011_no_error_all_zeros(self):
        """Test valid all-zero codeword."""
        encoder = bch_encoder()
        decoder = bch_decoder()
        
        # Use encoder to generate valid all-zero codeword
        payload = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        encoded_pdu = self._encode_payload(encoder, payload)
        valid_codeword = list(pmt.u8vector_elements(pmt.cdr(encoded_pdu)))
        
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, valid_codeword))
        captured = self._run_and_capture(decoder, msg)
        
        self.assertEqual(len(captured), 1)
        _, out_msg = captured[0]
        corrected_data = list(pmt.u8vector_elements(pmt.cdr(out_msg)))
        
        # Should have no error flag
        self.assertFalse(pmt.dict_has_key(pmt.car(out_msg), pmt.intern("bch_error")))
        self.assertEqual(payload, corrected_data)

    def test_012_1bit_error_corrected(self):
        """Test 1-bit error is corrected."""
        encoder = bch_encoder()
        decoder = bch_decoder()
        
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE]
        encoded_pdu = self._encode_payload(encoder, payload)
        valid_codeword = list(pmt.u8vector_elements(pmt.cdr(encoded_pdu)))
        
        # Corrupt with 1-bit error
        corrupted_codeword = valid_codeword.copy()
        corrupted_codeword[0] ^= 0b00000001
        
        msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, corrupted_codeword))
        captured = self._run_and_capture(decoder, msg)
        
        self.assertEqual(len(captured), 1)
        _, out_msg = captured[0]
        corrected_data = list(pmt.u8vector_elements(pmt.cdr(out_msg)))
        
        # Should be corrected (no error flag)
        self.assertFalse(pmt.dict_has_key(pmt.car(out_msg), pmt.intern("bch_error")))
        self.assertEqual(payload, corrected_data)

    def test_013_metadata_preserved_no_error(self):
        """Test metadata is preserved for valid codeword."""
        encoder = bch_encoder()
        decoder = bch_decoder()
        
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE]
        encoded_pdu = self._encode_payload(encoder, payload)
        valid_codeword = list(pmt.u8vector_elements(pmt.cdr(encoded_pdu)))
        
        # Create dict with metadata
        meta_dict = pmt.make_dict()
        meta_dict = pmt.dict_add(meta_dict, pmt.intern("timestamp"), pmt.from_long(12345))
        meta_dict = pmt.dict_add(meta_dict, pmt.intern("sequence"), pmt.from_long(42))
        
        msg = pmt.cons(meta_dict, pmt.init_u8vector(8, valid_codeword))
        captured = self._run_and_capture(decoder, msg)
        
        self.assertEqual(len(captured), 1)
        _, out_msg = captured[0]
        out_dict = pmt.car(out_msg)
        corrected_data = list(pmt.u8vector_elements(pmt.cdr(out_msg)))
        
        # Data should match
        self.assertEqual(payload, corrected_data)
        
        # Metadata should be preserved
        self.assertTrue(pmt.dict_has_key(out_dict, pmt.intern("timestamp")))
        self.assertTrue(pmt.dict_has_key(out_dict, pmt.intern("sequence")))


if __name__ == '__main__':
    gr_unittest.run(qa_bch_decoder)
