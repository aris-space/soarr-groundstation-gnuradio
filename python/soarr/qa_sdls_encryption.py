#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from unittest.mock import patch

from gnuradio import gr, gr_unittest
from Crypto.Cipher import AES
import pmt

from gnuradio.soarr import sdls_encryption

class qa_sdls_encryption(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.block = sdls_encryption(encryption_state=True, nonce=b"\x00" * 14)
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
        instance = sdls_encryption()
        self.assertIsNotNone(instance)

    def test_001_state_false_passthrough(self):
        self.block.encryption_state = False
        msg = self._make_pdu(bytes([9, 8]), counter=5)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, bytes([9, 8]))

    def test_002_aes_ctr_encrypts_payload_and_removes_key(self):
        self.block.encryption_state = True
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x1234
        payload = bytes([1, 2, 3, 4])
        msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("crypt_key")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        expected = AES.new(
            bytes.fromhex(key_hex),
            AES.MODE_CTR,
            nonce=b"\x00" * 14,
            initial_value=counter,
        ).encrypt(payload)
        self.assertEqual(out_payload, expected)

    def test_003_invalid_key_length_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), key_hex="0011", counter=1)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_004_counter_overflow_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), counter=65536)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_005_missing_crypt_key_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
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
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_007_non_dict_meta_no_output(self):
        msg = self._make_pdu_from_parts(pmt.PMT_T, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_008_non_u8vector_payload_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("crypt_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.from_long(123))

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_009_nonce_validation_in_constructor(self):
        with self.assertRaises(ValueError):
            sdls_encryption(nonce=b"\x00" * 13)

    def test_010_nonce_type_validation_in_constructor(self):
        with self.assertRaises(TypeError):
            sdls_encryption(nonce=0x1234)

    def test_011_counter_changes_ciphertext(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        payload = bytes([0xAA, 0xBB, 0xCC, 0xDD])

        msg_1 = self._make_pdu(payload, key_hex=key_hex, counter=1)
        msg_2 = self._make_pdu(payload, key_hex=key_hex, counter=2)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg_1)
            self.block.add_encryption(msg_2)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 2)
        c1 = bytes(pmt.u8vector_elements(pmt.cdr(self.published[0][1])))
        c2 = bytes(pmt.u8vector_elements(pmt.cdr(self.published[1][1])))
        self.assertNotEqual(c1, c2)

    def test_012_encrypt_then_decrypt_round_trip(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x4242
        plaintext = bytes([0x10, 0x20, 0x30, 0x40, 0xAA, 0x55])

        msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        _, out_msg = self.published[0]
        ciphertext = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # AES-CTR decryption uses the same key/nonce/counter parameters.
        recovered = AES.new(
            bytes.fromhex(key_hex),
            AES.MODE_CTR,
            nonce=b"\x00" * 14,
            initial_value=counter,
        ).decrypt(ciphertext)

        self.assertEqual(recovered, plaintext)

    # Additional: an internal failure past key/counter extraction (e.g. a
    # future encryption-library incompatibility) is caught, logged, and
    # dropped - not left to raise out of the real message handler.
    def test_013_internal_encryption_failure_is_dropped_not_raised(self):
        msg = self._make_pdu(bytes([1, 2, 3]), counter=1)

        original_pub = self._capture_pub()
        try:
            with patch.object(
                sdls_encryption,
                "_encrypt_payload",
                side_effect=RuntimeError("simulated encryption failure"),
            ):
                self.block.add_encryption(msg)  # must not raise
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # Additional: a publish failure in the encryption_state=False
    # passthrough branch is also caught and dropped, not left to raise -
    # the encrypted-path publish (test_013) isn't the only publish call
    # in this handler.
    def test_014_passthrough_publish_failure_is_dropped_not_raised(self):
        self.block.encryption_state = False
        msg = self._make_pdu(bytes([9, 8]), counter=5)

        original_pub = self.block.message_port_pub

        def _raise(port, out_msg):
            raise RuntimeError("simulated publish failure")

        self.block.message_port_pub = _raise
        try:
            self.block.add_encryption(msg)  # must not raise
        finally:
            self.block.message_port_pub = original_pub

    def test_015_state_false_still_rejects_non_pdu(self):
        # Shape validation must run regardless of encryption_state, so a
        # malformed message is never blindly republished when disabled.
        self.block.encryption_state = False

        original_pub = self._capture_pub()
        try:
            self.block.add_encryption(pmt.PMT_NIL)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_encryption)
