#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import pmt

from gnuradio.soarr import sdlsAuthentication, sdlsAuthenticationVerify


class qa_AuthenticateAuthVerify(gr_unittest.TestCase):

	def setUp(self):
		self.tb = gr.top_block()
		self.auth = sdlsAuthentication(state=True, nonce=b"\x00" * 14)
		self.verify = sdlsAuthenticationVerify(authentication_state=True, nonce=b"\x00" * 14)
		self.published = []

	def tearDown(self):
		self.tb = None
		self.auth = None
		self.verify = None
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
		meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
		return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

	def _run_authenticate(self, msg):
		self.published = []
		original_pub = self._capture_pub(self.auth)
		try:
			self.auth.add_authentication(msg)
		finally:
			self._restore_pub(self.auth, original_pub)

		if not self.published:
			return None
		return self.published.pop(0)[1]

	def _run_verify(self, msg):
		self.published = []
		original_pub = self._capture_pub(self.verify)
		try:
			self.verify.verify_message(msg)
		finally:
			self._restore_pub(self.verify, original_pub)

		if not self.published:
			return None
		return self.published.pop(0)[1]

	def _run_verify_with(self, block, msg):
		self.published = []
		original_pub = self._capture_pub(block)
		try:
			block.verify_message(msg)
		finally:
			self._restore_pub(block, original_pub)

		if not self.published:
			return None
		return self.published.pop(0)[1]

	def test_instance(self):
		self.assertIsNotNone(self.auth)
		self.assertIsNotNone(self.verify)

	def test_001_authenticate_then_verify_round_trip(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x1234
		payload = bytes([0x10, 0x20, 0x30, 0x40])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)

		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		out_msg = self._run_verify(auth_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertEqual(out_payload, payload)

	def test_002_tamper_tag_fails_verification(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x2222
		payload = bytes([1, 2, 3, 4, 5])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)
		cipher_payload = bytearray(pmt.u8vector_elements(pmt.cdr(auth_msg)))
		cipher_payload[-1] ^= 0x01
		bad_payload = pmt.init_u8vector(len(cipher_payload), list(cipher_payload))
		auth_msg = pmt.cons(pmt.car(auth_msg), bad_payload)

		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		out_msg = self._run_verify(auth_msg)
		self.assertIsNone(out_msg)

	def test_003_auth_disabled_verify_enabled(self):
		self.auth.state = False
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x3333
		payload = bytes([9, 8, 7])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)

		out_msg = self._run_verify(auth_msg)
		self.assertIsNone(out_msg)

	def test_004_verify_disabled_passthrough(self):
		self.verify.authentication_state = False
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x4444
		payload = bytes([0xAA, 0xBB])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)

		out_msg = self._run_verify(auth_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertEqual(out_payload, bytes(pmt.u8vector_elements(pmt.cdr(auth_msg))))

	def test_005_nonce_mismatch_fails(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x5555
		payload = bytes([0x01, 0x02, 0x03])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)
		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		verify_mismatch = sdlsAuthenticationVerify(authentication_state=True, nonce=b"\x01" * 14)
		out_msg = self._run_verify_with(verify_mismatch, auth_msg)
		self.assertIsNone(out_msg)

	def test_006_counter_mismatch_fails(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x6666
		payload = bytes([0x0A, 0x0B, 0x0C])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)
		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("sdls_counter"), pmt.from_long(counter + 1))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		out_msg = self._run_verify(auth_msg)
		self.assertIsNone(out_msg)

	def test_007_payload_tamper_fails(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x7777
		payload = bytes([0x10, 0x20, 0x30, 0x40])
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)
		tagged = bytearray(pmt.u8vector_elements(pmt.cdr(auth_msg)))
		tagged[0] ^= 0x01
		tampered_payload = pmt.init_u8vector(len(tagged), list(tagged))
		auth_msg = pmt.cons(pmt.car(auth_msg), tampered_payload)

		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		out_msg = self._run_verify(auth_msg)
		self.assertIsNone(out_msg)

	def test_008_empty_payload_round_trip(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		counter = 0x8888
		payload = b""
		msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

		auth_msg = self._run_authenticate(msg)
		self.assertIsNotNone(auth_msg)

		auth_meta = pmt.car(auth_msg)
		auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
		auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

		out_msg = self._run_verify(auth_msg)
		self.assertIsNotNone(out_msg)
		out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
		self.assertEqual(out_payload, payload)

	def test_009_multiple_messages_round_trip(self):
		key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
		payloads = [bytes([1, 2]), bytes([3, 4, 5]), bytes([6])]

		for idx, payload in enumerate(payloads, start=1):
			msg = self._make_pdu(payload, key_hex=key_hex, counter=idx)
			auth_msg = self._run_authenticate(msg)
			self.assertIsNotNone(auth_msg)

			auth_meta = pmt.car(auth_msg)
			auth_meta = pmt.dict_add(auth_meta, pmt.intern("auth_key"), pmt.intern(key_hex))
			auth_msg = pmt.cons(auth_meta, pmt.cdr(auth_msg))

			out_msg = self._run_verify(auth_msg)
			self.assertIsNotNone(out_msg)
			out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
			self.assertEqual(out_payload, payload)


if __name__ == '__main__':
	gr_unittest.run(qa_AuthenticateAuthVerify)
