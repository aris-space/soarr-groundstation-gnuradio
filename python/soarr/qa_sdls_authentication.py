#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from unittest.mock import patch

from gnuradio import gr, gr_unittest
from Crypto.Hash import CMAC
from Crypto.Cipher import AES
import pmt

from gnuradio.soarr import sdls_authentication

class qa_sdls_authentication(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.block = sdls_authentication(authentication_state=True)
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

    def _make_pdu(self, payload_bytes, key_hex="00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF", counter=1):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(key_hex))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(counter))
        return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

    def _make_pdu_from_parts(self, meta, payload_obj):
        return pmt.cons(meta, payload_obj)

    def _verify_tag(self, key_hex, counter, payload, tag):
        counter_bytes = (b"\x00" * 14) + counter.to_bytes(2, byteorder="big", signed=False)
        verifier = CMAC.new(bytes.fromhex(key_hex), ciphermod=AES)
        verifier.update(counter_bytes + payload)
        try:
            verifier.verify(tag)
            return True
        except ValueError:
            return False

    def test_instance(self):
        instance = sdls_authentication()
        self.assertIsNotNone(instance)

    def test_001_state_false_passthrough(self):
        self.block.authentication_state = False
        msg = self._make_pdu(bytes([9, 8]), counter=5)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, bytes([9, 8]))

    def test_002_authentication_appends_tag_without_encrypting_payload(self):
        self.block.authentication_state = True
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 1
        payload = bytes([1, 2, 3, 4])
        msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("auth_key")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload[: len(payload)], payload)

        counter_bytes = (b"\x00" * 14) + counter.to_bytes(2, byteorder="big", signed=False)
        expected_cmac = CMAC.new(bytes.fromhex(key_hex), ciphermod=AES)
        expected_cmac.update(counter_bytes + payload)
        expected_tag = expected_cmac.digest()
        self.assertEqual(out_payload[len(payload) :], expected_tag)
        self.assertEqual(len(out_payload), len(payload) + 16)

    def test_003_invalid_key_length_no_output(self):
        self.block.authentication_state = True
        msg = self._make_pdu(bytes([1, 2, 3]), key_hex="0011", counter=1)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_004_counter_overflow_no_output(self):
        self.block.authentication_state = True
        msg = self._make_pdu(bytes([1, 2, 3]), counter=65536)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_005_missing_auth_key_no_output(self):
        self.block.authentication_state = True
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_006_missing_counter_no_output(self):
        self.block.authentication_state = True
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("auth_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        msg = self._make_pdu_from_parts(meta, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_007_non_dict_meta_no_output(self):
        self.block.authentication_state = True
        msg = self._make_pdu_from_parts(pmt.PMT_T, pmt.init_u8vector(2, [1, 2]))

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_008_non_u8vector_payload_no_output(self):
        self.block.authentication_state = True
        meta = pmt.make_dict()
        meta = pmt.dict_add(
            meta,
            pmt.intern("auth_key"),
            pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
        )
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(1))
        msg = self._make_pdu_from_parts(meta, pmt.from_long(123))

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_009_authentication_tag_changes_with_counter(self):
        self.block.authentication_state = True
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        payload = bytes([0xAA, 0xBB, 0xCC, 0xDD])

        msg_1 = self._make_pdu(payload, key_hex=key_hex, counter=1)
        msg_2 = self._make_pdu(payload, key_hex=key_hex, counter=2)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg_1)
            self.block.add_authentication(msg_2)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 2)

        out_payload_1 = bytes(pmt.u8vector_elements(pmt.cdr(self.published[0][1])))
        out_payload_2 = bytes(pmt.u8vector_elements(pmt.cdr(self.published[1][1])))

        tag_1 = out_payload_1[len(payload):]
        tag_2 = out_payload_2[len(payload):]
        self.assertNotEqual(tag_1, tag_2)

    def test_010_receiver_verifies_tag_and_detects_tamper(self):
        self.block.authentication_state = True
        key_hex = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
        counter = 0x1234
        payload = bytes([0x10, 0x20, 0x30, 0x40, 0x50])
        msg = self._make_pdu(payload, key_hex=key_hex, counter=counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_authentication(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        _, out_msg = self.published[0]
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        rx_payload = out_payload[:len(payload)]
        rx_tag = out_payload[len(payload):]

        # Positive check: receiver can verify emitted tag.
        self.assertTrue(self._verify_tag(key_hex, counter, rx_payload, rx_tag))

        # Negative check: altered payload must fail verification.
        tampered_payload = bytearray(rx_payload)
        tampered_payload[0] ^= 0x01
        self.assertFalse(self._verify_tag(key_hex, counter, bytes(tampered_payload), rx_tag))

    # Additional: an internal failure past key/counter extraction (e.g. a
    # future crypto-library incompatibility) is caught, logged, and
    # dropped - not left to raise out of the real message handler.
    def test_011_internal_tag_failure_is_dropped_not_raised(self):
        self.block.authentication_state = True
        msg = self._make_pdu(bytes([1, 2, 3]), counter=1)

        original_pub = self._capture_pub()
        try:
            with patch.object(
                sdls_authentication,
                "_get_authentication_tag",
                side_effect=RuntimeError("simulated tag failure"),
            ):
                self.block.add_authentication(msg)  # must not raise
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # Additional: a publish failure in the authentication_state=False
    # passthrough branch is also caught and dropped, not left to raise -
    # the tagging-path publish (test_011) isn't the only publish call in
    # this handler.
    def test_012_passthrough_publish_failure_is_dropped_not_raised(self):
        self.block.authentication_state = False
        msg = self._make_pdu(bytes([9, 8]), counter=5)

        original_pub = self.block.message_port_pub

        def _raise(port, out_msg):
            raise RuntimeError("simulated publish failure")

        self.block.message_port_pub = _raise
        try:
            self.block.add_authentication(msg)  # must not raise
        finally:
            self.block.message_port_pub = original_pub


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_authentication)
