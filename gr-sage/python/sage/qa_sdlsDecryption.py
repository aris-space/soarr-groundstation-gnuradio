#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from Crypto.Cipher import AES
import pmt

from gnuradio.sage import sdlsDecryption

class qa_sdlsDecryption(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.block = sdlsDecryption(decryption_state=True, nonce=b"\x00" * 14)
        self.published = []

    def tearDown(self):
        self.tb = None
        self.block = None
        self.published = []

    def _capture_pub(self):
        original_pub = self.block.message_port_pub

        def _capture(port, msg):
            self.published.append((port, msg))

        self.block.message_port_pub = _capture
        return original_pub

    def _restore_pub(self, original_pub):
        self.block.message_port_pub = original_pub

    def _make_pdu(self, payload_bytes, key_hex=None, counter=1):
        if key_hex is None:
            key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
        return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

    def _make_pdu_from_parts(self, meta, payload_obj):
        return pmt.cons(meta, payload_obj)

    def test_instance(self):
        instance = sdlsDecryption()
        self.assertIsNotNone(instance)

    def test_001_state_false_passthrough(self):
        self.block.decryption_state = False
        msg = self._make_pdu(bytes([9, 8]), counter=5)

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, bytes([9, 8]))

    def test_002_aes_ctr_decrypts_payload_and_removes_key(self):
        self.block.decryption_state = True
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x1234
        plaintext = bytes([1, 2, 3, 4])
        ciphertext = AES.new(
            bytes.fromhex(key_hex),
            AES.MODE_CTR,
            nonce=b"\x00" * 14,
            initial_value=counter,
        ).encrypt(plaintext)
        msg = self._make_pdu(ciphertext, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("crypt_key")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, plaintext)

    def test_003_invalid_key_length_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), key_hex="0011", counter=1)

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_004_counter_overflow_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), counter=65536)

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_005_missing_crypt_key_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_006_missing_counter_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("crypt_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.decrypt_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_007_non_dict_meta_no_output(self):
        msg = self._make_pdu_from_parts(pmt.PMT_T, pmt.init_u8vector(2, [1, 2]))

        with self.assertRaises(ValueError):
            self.block.decrypt_message(msg)

    def test_008_non_u8vector_payload_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("crypt_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.from_long(123))

        with self.assertRaises(ValueError):
            self.block.decrypt_message(msg)

    def test_009_nonce_validation_in_constructor(self):
        with self.assertRaises(ValueError):
            sdlsDecryption(nonce=b"\x00" * 13)

    def test_010_nonce_type_validation_in_constructor(self):
        with self.assertRaises(TypeError):
            sdlsDecryption(nonce=0x1234)


if __name__ == '__main__':
    gr_unittest.run(qa_sdlsDecryption)
