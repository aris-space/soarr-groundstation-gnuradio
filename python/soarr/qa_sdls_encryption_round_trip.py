#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import pmt

from gnuradio.soarr import sdls_decryption, sdls_encryption


class qa_sdls_encryption_round_trip(gr_unittest.TestCase):

	def setUp(self):
		self.tb = gr.top_block()
		self.enc = sdls_encryption(encryption_state=True, nonce=b"\x00" * 14)
		self.dec = sdls_decryption(decryption_state=True, nonce=b"\x00" * 14)
		self.published = []

	def tearDown(self):
		self.tb = None
		self.enc = None
		self.dec = None
		self.published = []

	def _capture_pub(self, block):
		original_pub = block.message_port_pub

		def _capture(port, msg):
			self.published.append((port, msg))

		block.message_port_pub = _capture
		return original_pub

	def _restore_pub(self, block, original_pub):
		block.message_port_pub = original_pub

	def _make_pdu(self, payload_bytes, key_hex=None, counter=1):
		if key_hex is None:
			key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		meta = pmt.make_dict()
		meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
		meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
		return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

	def _run_encrypt(self, msg):
		self.published = []
		enc_original_pub = self._capture_pub(self.enc)
		try:
			self.enc.add_encryption(msg)
		finally:
			self._restore_pub(self.enc, enc_original_pub)

		if not self.published:
			return None
		return self.published.pop(0)[1]

	def _run_decrypt(self, msg, block=None):
		if block is None:
			block = self.dec
		self.published = []
		dec_original_pub = self._capture_pub(block)
		try:
			block.decrypt_message(msg)
		finally:
			self._restore_pub(block, dec_original_pub)

		if not self.published:
			return None
		return self.published.pop(0)[1]

	def test_instance(self):
		self.assertIsNotNone(self.enc)
		self.assertIsNotNone(self.dec)

	def test_001_encrypt_then_decrypt_round_trip(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x2222
		plaintext = bytes([0x10, 0x20, 0x30, 0x40, 0x55, 0xAA])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)
		enc_meta = pmt.car(enc_msg)
		enc_meta = pmt.dict_add(enc_meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
		enc_msg = pmt.cons(enc_meta, pmt.cdr(enc_msg))

		out_msg = self._run_decrypt(enc_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertEqual(out_payload, plaintext)

	def test_002_wrong_key_changes_plaintext(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		wrong_key = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
		counter = 0x1111
		plaintext = bytes([1, 2, 3, 4, 5, 6])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)
		enc_meta = pmt.car(enc_msg)
		enc_meta = pmt.dict_add(enc_meta, pmt.intern("crypt_key"), pmt.intern(wrong_key))
		enc_msg = pmt.cons(enc_meta, pmt.cdr(enc_msg))

		out_msg = self._run_decrypt(enc_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertNotEqual(out_payload, plaintext)

	def test_003_wrong_counter_changes_plaintext(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x3333
		wrong_counter = 0x3334
		plaintext = bytes([0xAA, 0xBB, 0xCC, 0xDD])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)
		enc_meta = pmt.car(enc_msg)
		enc_meta = pmt.dict_add(enc_meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
		enc_meta = pmt.dict_add(enc_meta, pmt.intern("sdls_counter"), pmt.from_long(wrong_counter))
		enc_msg = pmt.cons(enc_meta, pmt.cdr(enc_msg))

		out_msg = self._run_decrypt(enc_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertNotEqual(out_payload, plaintext)

	def test_004_missing_crypt_key_no_output(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x4444
		plaintext = bytes([7, 8, 9])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)

		out_msg = self._run_decrypt(enc_msg)
		self.assertIsNone(out_msg)

	def test_005_nonce_mismatch_changes_plaintext(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x5555
		plaintext = bytes([0x01, 0x02, 0x03, 0x04])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)
		enc_meta = pmt.car(enc_msg)
		enc_meta = pmt.dict_add(enc_meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
		enc_msg = pmt.cons(enc_meta, pmt.cdr(enc_msg))

		dec_mismatch = sdls_decryption(decryption_state=True, nonce=b"\x01" * 14)
		out_msg = self._run_decrypt(enc_msg, block=dec_mismatch)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertNotEqual(out_payload, plaintext)

	def test_006_multiple_messages_round_trip(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		payloads = [bytes([1, 2, 3]), bytes([4, 5, 6, 7])]

		for idx, payload in enumerate(payloads, start=1):
			msg = self._make_pdu(payload, key_hex=key_hex, counter=idx)
			enc_msg = self._run_encrypt(msg)
			self.assertIsNotNone(enc_msg)
			enc_meta = pmt.car(enc_msg)
			enc_meta = pmt.dict_add(enc_meta, pmt.intern("crypt_key"), pmt.intern(key_hex))
			enc_msg = pmt.cons(enc_meta, pmt.cdr(enc_msg))

			out_msg = self._run_decrypt(enc_msg)
			self.assertIsNotNone(out_msg)
			out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
			self.assertEqual(out_payload, payload)

	def test_007_both_blocks_disabled_passthrough(self):
		self.enc.encryption_state = False
		self.dec.decryption_state = False
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x6666
		plaintext = bytes([0xAB, 0xCD])
		msg = self._make_pdu(plaintext, key_hex=key_hex, counter=counter)

		enc_msg = self._run_encrypt(msg)
		self.assertIsNotNone(enc_msg)

		out_msg = self._run_decrypt(enc_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertEqual(out_payload, plaintext)


if __name__ == '__main__':
	gr_unittest.run(qa_sdls_encryption_round_trip)
