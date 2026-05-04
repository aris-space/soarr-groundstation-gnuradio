#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import pmt
from gnuradio.sage import bchEncoder, bchDecoder


class qa_bchEncoderDecoder(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.encoder = bchEncoder()
        self.decoder = bchDecoder()

    def tearDown(self):
        self.tb = None

    def _capture_message_port(self, dut, method_name, pdu):
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            getattr(dut, method_name)(pdu)
        finally:
            dut.message_port_pub = original_pub

        return captured

    def _encode(self, payload_bytes, meta=None):
        if meta is None:
            meta = pmt.make_dict()

        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))
        captured = self._capture_message_port(self.encoder, "encodeBCH", pmt.cons(meta, payload))
        self.assertEqual(len(captured), 1)
        port, msg = captured[0]
        self.assertEqual(pmt.symbol_to_string(port), "codewords")
        return msg

    def _decode(self, pdu):
        captured = self._capture_message_port(self.decoder, "error_correction_mode", pdu)
        self.assertEqual(len(captured), 1)
        port, msg = captured[0]
        self.assertEqual(pmt.symbol_to_string(port), "out")
        return msg

    def test_instance(self):
        self.assertIsInstance(self.encoder, bchEncoder)
        self.assertIsInstance(self.decoder, bchDecoder)

    def test_001_round_trip_valid_codeword(self):
        payload_bytes = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        encoded = self._encode(payload_bytes)
        decoded = self._decode(encoded)

        encoded_bytes = bytes(pmt.u8vector_elements(pmt.cdr(encoded)))
        decoded_bytes = bytes(pmt.u8vector_elements(pmt.cdr(decoded)))
        self.assertEqual(encoded_bytes, bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF8]))
        self.assertEqual(decoded_bytes, payload_bytes)
        self.assertFalse(pmt.dict_has_key(pmt.car(decoded), pmt.intern("bch_error")))

    def test_002_single_bit_error_is_corrected(self):
        payload_bytes = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        encoded = self._encode(payload_bytes)
        original_codeword = bytes(pmt.u8vector_elements(pmt.cdr(encoded)))
        corrupted = bytearray(original_codeword)
        corrupted[0] ^= 0x80
        corrupted_pdu = pmt.cons(pmt.car(encoded), pmt.init_u8vector(len(corrupted), list(corrupted)))

        decoded = self._decode(corrupted_pdu)
        decoded_bytes = bytes(pmt.u8vector_elements(pmt.cdr(decoded)))
        self.assertEqual(decoded_bytes, payload_bytes)
        self.assertFalse(pmt.dict_has_key(pmt.car(decoded), pmt.intern("bch_error")))

    def test_003_two_bit_error_is_corrected(self):
        payload_bytes = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])
        encoded = self._encode(payload_bytes)
        original_codeword = bytes(pmt.u8vector_elements(pmt.cdr(encoded)))
        corrupted = bytearray(original_codeword)
        corrupted[0] ^= 0x80
        corrupted[1] ^= 0x20
        corrupted_pdu = pmt.cons(pmt.car(encoded), pmt.init_u8vector(len(corrupted), list(corrupted)))

        decoded = self._decode(corrupted_pdu)
        decoded_bytes = bytes(pmt.u8vector_elements(pmt.cdr(decoded)))
        self.assertEqual(decoded_bytes, payload_bytes)
        self.assertFalse(pmt.dict_has_key(pmt.car(decoded), pmt.intern("bch_error")))

    def test_004_metadata_is_preserved(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("source"), pmt.intern("integration-test"))
        payload_bytes = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE])

        encoded = self._encode(payload_bytes, meta=meta)
        decoded = self._decode(encoded)

        self.assertTrue(pmt.dict_has_key(pmt.car(decoded), pmt.intern("source")))
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(decoded))), payload_bytes)


if __name__ == '__main__':
    gr_unittest.run(qa_bchEncoderDecoder)
