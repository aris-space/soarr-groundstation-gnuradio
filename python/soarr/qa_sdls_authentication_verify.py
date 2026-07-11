#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from Crypto.Hash import CMAC
from Crypto.Cipher import AES
import pmt

from gnuradio.soarr import sdls_authentication_verify


TAG_LEN = 16

class qa_sdls_authentication_verify(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.block = sdls_authentication_verify(authentication_state=True, nonce=b"\x00" * 14)
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
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(key_hex))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
        return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

    def _make_pdu_from_parts(self, meta, payload_obj):
        return pmt.cons(meta, payload_obj)

    def _make_tagged_payload(self, key_hex, counter, payload):
        counter_bytes = (b"\x00" * 14) + counter.to_bytes(2, byteorder="big", signed=False)
        cobj = CMAC.new(bytes.fromhex(key_hex), ciphermod=AES)
        cobj.update(counter_bytes + payload)
        tag = cobj.digest()
        return payload + tag

    def _make_trailer_tag(self, key_hex, counter, payload):
        counter_bytes = (b"\x00" * 14) + counter.to_bytes(2, byteorder="big", signed=False)
        cobj = CMAC.new(bytes.fromhex(key_hex), ciphermod=AES)
        cobj.update(counter_bytes + payload)
        return cobj.digest()

    def _encap_meta(self, packet_length=0x0104):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("length_of_length"), pmt.from_long(2))
        meta = pmt.dict_add(meta, pmt.intern("packet_version"), pmt.from_long(7))
        meta = pmt.dict_add(meta, pmt.intern("protocol_id"), pmt.from_long(7))
        meta = pmt.dict_add(meta, pmt.intern("user_defined_field"), pmt.from_long(0))
        meta = pmt.dict_add(meta, pmt.intern("protocol_id_extension"), pmt.from_long(0))
        meta = pmt.dict_add(meta, pmt.intern("first_octet"), pmt.from_long(0xFE))
        meta = pmt.dict_add(meta, pmt.intern("packet_length"), pmt.from_long(packet_length))
        return meta

    def _encap_bytes(self, packet_length=0x0104):
        return bytes([0xFE, 0x00]) + int(packet_length).to_bytes(2, byteorder="big", signed=False)

    def test_instance(self):
        instance = sdls_authentication_verify()
        self.assertIsNotNone(instance)

    def test_001_state_false_passthrough(self):
        self.block.authentication_state = False
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 1
        payload = bytes([9, 8])
        tagged_payload = self._make_tagged_payload(key_hex, counter, payload)
        msg = self._make_pdu(tagged_payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, tagged_payload)

    def test_002_valid_tag_outputs_payload_and_removes_key(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x1234
        payload = bytes([1, 2, 3, 4])
        tagged_payload = self._make_tagged_payload(key_hex, counter, payload)
        msg = self._make_pdu(tagged_payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("auth_key")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, payload)

    def test_003_invalid_key_length_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), key_hex="0011", counter=1)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_004_counter_overflow_no_output(self):
        msg = self._make_pdu(bytes([1, 2, 3]), counter=65536)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_005_missing_auth_key_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_006_missing_counter_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("auth_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_007_non_dict_meta_raises(self):
        msg = self._make_pdu_from_parts(pmt.PMT_T, pmt.init_u8vector(2, [1, 2]))
        with self.assertRaises(ValueError):
            self.block.verify_message(msg)

    def test_008_non_u8vector_payload_raises(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("auth_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.from_long(123))
        with self.assertRaises(ValueError):
            self.block.verify_message(msg)

    def test_009_invalid_tag_no_output(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x4321
        payload = bytes([1, 2, 3, 4])
        tagged_payload = self._make_tagged_payload(key_hex, counter, payload)
        tagged_payload = tagged_payload[:-1] + bytes([tagged_payload[-1] ^ 0x01])
        msg = self._make_pdu(tagged_payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_010_payload_too_short_no_output(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x1111
        payload = bytes([1, 2, 3])
        msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_011_nonce_validation_in_constructor(self):
        with self.assertRaises(ValueError):
            sdls_authentication_verify(nonce=b"\x00" * 13)

    def test_012_nonce_type_validation_in_constructor(self):
        with self.assertRaises(TypeError):
            sdls_authentication_verify(nonce=0x1234)

    def test_013_trailer_with_encap_in_payload_strips_header(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 5
        payload = bytes([0x01, 0x02, 0x03, 0x04])
        encap = self._encap_bytes()
        tag = self._make_trailer_tag(key_hex, counter, encap + payload)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(key_hex))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
        meta = pmt.dict_add(meta, pmt.intern("encapsulation_header"), self._encap_meta())
        sdls_meta = pmt.make_dict()
        sdls_meta = pmt.dict_add(sdls_meta, pmt.intern("security_trailer"), pmt.init_u8vector(len(tag), list(tag)))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), sdls_meta)

        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(len(encap + payload), list(encap + payload)))

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, payload)

    def test_014_trailer_with_no_encap_in_payload_keeps_payload(self):
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 6
        payload = bytes([0x0A, 0x0B, 0x0C])
        encap = self._encap_bytes()
        tag = self._make_trailer_tag(key_hex, counter, encap + payload)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(key_hex))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
        meta = pmt.dict_add(meta, pmt.intern("encapsulation_header"), self._encap_meta())
        sdls_meta = pmt.make_dict()
        sdls_meta = pmt.dict_add(sdls_meta, pmt.intern("security_trailer"), pmt.init_u8vector(len(tag), list(tag)))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), sdls_meta)

        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(len(payload), list(payload)))

        original_pub = self._capture_pub()
        try:
            self.block.verify_message(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, payload)


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_authentication_verify)
