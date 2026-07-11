#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import encapsulation_header


class qa_encapsulation_header(gr_unittest.TestCase):

    def setUp(self):
        self.block = encapsulation_header(user_defined_field=0b1010)
        self.published = []

    def tearDown(self):
        self.block = None
        self.published = []

    def test_instance(self):
        instance = encapsulation_header()
        self.assertIsNotNone(instance)

    def _capture_pub(self):
        original_pub = self.block.message_port_pub

        def _capture(port, msg):
            self.published.append((port, msg))

        self.block.message_port_pub = _capture
        return original_pub

    def _restore_pub(self, original_pub):
        self.block.message_port_pub = original_pub

    def _make_pdu(self, metadata, payload_bytes):
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))
        return pmt.cons(metadata, payload)

    def _default_meta(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(42))
        return meta

    def _split_header_payload(self, framed_bytes):
        length_of_length = framed_bytes[0] & 0b11
        if length_of_length == 0b00:
            header_len = 1
        elif length_of_length == 0b01:
            header_len = 2
        elif length_of_length == 0b10:
            header_len = 4
        else:
            header_len = 8
        return framed_bytes[:header_len], framed_bytes[header_len:]

    def _get_single_output(self):
        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_meta = pmt.car(out_msg)
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        return out_meta, out_payload

    # 1) error check: non-PDU
    def test_001_rejects_non_pdu_input(self):
        original_pub = self._capture_pub()
        try:
            self.block.add_header(pmt.PMT_T)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # 2) error check: non-dict metadata
    def test_002_rejects_non_dict_metadata(self):
        payload = pmt.init_u8vector(3, [0x01, 0x02, 0x03])
        msg = pmt.cons(pmt.from_long(7), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # 3) error check: non-u8vector payload
    def test_003_rejects_non_u8vector_payload(self):
        msg = pmt.cons(self._default_meta(), pmt.from_long(99))

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # 4) empty payload: check header format
    def test_004_empty_payload_header_format(self):
        msg = self._make_pdu(self._default_meta(), b"")

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        header, payload = self._split_header_payload(framed)

        self.assertEqual(payload, b"")
        self.assertEqual(len(header), 1)
        self.assertEqual(header[0] >> 5, 0b111)           # packet version number
        self.assertEqual((header[0] >> 2) & 0b111, 0b000) # idle protocol id
        self.assertEqual(header[0] & 0b11, 0b00)          # length_of_length

    # 5) payload size variant: LOL=01
    def test_005_payload_small_uses_lol_01(self):
        payload = bytes([0xAA])
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        header, out_payload = self._split_header_payload(framed)

        self.assertEqual(len(header), 2)
        self.assertEqual((header[0] >> 2) & 0b111, 0b111) # data protocol id
        self.assertEqual(header[0] & 0b11, 0b01)
        self.assertEqual(header[1], len(payload) + 2)
        self.assertEqual(out_payload, payload)

    # 6) payload size variant: LOL=10
    def test_006_payload_medium_uses_lol_10(self):
        payload = bytes([0x55] * 300)
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        header, out_payload = self._split_header_payload(framed)

        self.assertEqual(len(header), 4)
        self.assertEqual(header[0] & 0b11, 0b10)
        self.assertEqual(header[1] >> 4, 0b1010)   # user_defined_field
        self.assertEqual(header[1] & 0x0F, 0b0000) # protocol_id_extention
        self.assertEqual(int.from_bytes(header[2:4], "big"), len(payload) + 4)
        self.assertEqual(out_payload, payload)

    # 7) payload size variant: LOL=11
    def test_007_payload_large_uses_lol_11(self):
        payload = bytes([0x33] * 70000)
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        header, out_payload = self._split_header_payload(framed)

        self.assertEqual(len(header), 8)
        self.assertEqual(header[0] & 0b11, 0b11)
        self.assertEqual(header[1] >> 4, 0b1010)   # user_defined_field
        self.assertEqual(header[1] & 0x0F, 0b0000) # protocol_id_extention
        self.assertEqual(int.from_bytes(header[2:4], "big"), 0)        # CCSDS_DEFINED_FIELD
        self.assertEqual(int.from_bytes(header[4:8], "big"), len(payload) + 8)
        self.assertEqual(out_payload, payload)

    # 8) payload size boundary variant check
    def test_008_length_boundary_transitions(self):
        self.assertEqual(encapsulation_header._determine_length_of_length(bytes([0x00] * 253)), 0b01)
        self.assertEqual(encapsulation_header._determine_length_of_length(bytes([0x00] * 254)), 0b10)
        self.assertEqual(encapsulation_header._determine_length_of_length(bytes([0x00] * 65531)), 0b10)
        self.assertEqual(encapsulation_header._determine_length_of_length(bytes([0x00] * 65532)), 0b11)

    # 9) error raised when size is exceeded
    def test_009_raises_when_payload_size_exceeds_supported_limit(self):
        class OversizedObject:
            def __len__(self):
                return (2 ** 32) - 8

        with self.assertRaises(ValueError):
            encapsulation_header._determine_length_of_length(OversizedObject())

    # 10) block input handled correctly (valid PDU published on out)
    def test_010_valid_input_is_processed_and_published(self):
        payload = bytes([0x10, 0x20, 0x30])
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        out_meta, framed = self._get_single_output()
        header, out_payload = self._split_header_payload(framed)
        self.assertGreaterEqual(len(header), 2)
        self.assertEqual(out_payload, payload)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))

    # 11) input dict is not changed
    def test_011_metadata_dict_is_unchanged(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(17))
        meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(3))
        msg = self._make_pdu(meta, bytes([0x01, 0x02]))

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        out_meta, _ = self._get_single_output()
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("vcid")))
        self.assertTrue(pmt.eqv(pmt.dict_ref(out_meta, pmt.intern("frame_id"), pmt.PMT_NIL), pmt.from_long(17)))
        self.assertTrue(pmt.eqv(pmt.dict_ref(out_meta, pmt.intern("vcid"), pmt.PMT_NIL), pmt.from_long(3)))

    # Additional: strict fixed fields should not appear for LOL=01
    def test_012_lol_01_does_not_include_optional_fields(self):
        payload = bytes([0xAB] * 10)
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        header, _ = self._split_header_payload(framed)
        self.assertEqual(len(header), 2)

    # Additional: protocol id must be DATA when payload is non-empty
    def test_013_non_empty_payload_uses_data_protocol_id(self):
        payload = bytes([0x01])
        msg = self._make_pdu(self._default_meta(), payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        _, framed = self._get_single_output()
        self.assertEqual((framed[0] >> 2) & 0b111, 0b111)

    # Additional: user_defined_field below 0 should fail when field is present
    def test_014_negative_user_defined_field_raises(self):
        block = encapsulation_header(user_defined_field=-1)
        published = []

        original_pub = block.message_port_pub

        def _capture(port, msg):
            published.append((port, msg))

        block.message_port_pub = _capture
        try:
            msg = self._make_pdu(self._default_meta(), bytes([0x44] * 300))
            with self.assertRaises(Exception):
                block.add_header(msg)
        finally:
            block.message_port_pub = original_pub

        self.assertEqual(len(published), 0)

    # Additional: user_defined_field above 4-bit range should fail when field is present
    def test_015_too_large_user_defined_field_raises(self):
        block = encapsulation_header(user_defined_field=16)
        published = []

        original_pub = block.message_port_pub

        def _capture(port, msg):
            published.append((port, msg))

        block.message_port_pub = _capture
        try:
            msg = self._make_pdu(self._default_meta(), bytes([0x45] * 300))
            with self.assertRaises(Exception):
                block.add_header(msg)
        finally:
            block.message_port_pub = original_pub

        self.assertEqual(len(published), 0)

    # Additional: end-to-end boundary checks for LOL transitions
    def test_016_end_to_end_length_boundaries(self):
        cases = [
            (253, 0b01, 2),
            (254, 0b10, 4),
            (65531, 0b10, 4),
            (65532, 0b11, 8),
        ]

        original_pub = self._capture_pub()
        try:
            for size, expected_lol, expected_header_len in cases:
                self.published = []
                payload = bytes([0x5A] * size)
                msg = self._make_pdu(self._default_meta(), payload)
                self.block.add_header(msg)

                _, framed = self._get_single_output()
                header, out_payload = self._split_header_payload(framed)
                self.assertEqual(header[0] & 0b11, expected_lol)
                self.assertEqual(len(header), expected_header_len)
                self.assertEqual(out_payload, payload)
        finally:
            self._restore_pub(original_pub)

    # Additional: exact packet_length byte encoding at key boundaries
    def test_017_packet_length_bytes_exact(self):
        cases = [
            (253, 0b01, b"\xFF"),
            (254, 0b10, b"\x01\x02"),
            (65531, 0b10, b"\xFF\xFF"),
            (65532, 0b11, b"\x00\x01\x00\x04"),
        ]

        original_pub = self._capture_pub()
        try:
            for size, lol, expected_len_bytes in cases:
                self.published = []
                payload = bytes([0x6B] * size)
                msg = self._make_pdu(self._default_meta(), payload)
                self.block.add_header(msg)

                _, framed = self._get_single_output()
                header, _ = self._split_header_payload(framed)
                self.assertEqual(header[0] & 0b11, lol)

                if lol == 0b01:
                    self.assertEqual(header[1:2], expected_len_bytes)
                elif lol == 0b10:
                    self.assertEqual(header[2:4], expected_len_bytes)
                else:
                    self.assertEqual(header[4:8], expected_len_bytes)
        finally:
            self._restore_pub(original_pub)

    # Additional: metadata integrity across all header variants
    def test_018_metadata_integrity_across_variants(self):
        variants = [0, 1, 300, 70000]
        original_pub = self._capture_pub()
        try:
            for size in variants:
                self.published = []

                meta = pmt.make_dict()
                meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(77))
                meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(5))
                meta = pmt.dict_add(meta, pmt.intern("tag"), pmt.intern("A1"))
                payload = bytes([0x11] * size)

                msg = self._make_pdu(meta, payload)
                self.block.add_header(msg)

                out_meta, _ = self._get_single_output()
                self.assertTrue(pmt.eqv(pmt.dict_ref(out_meta, pmt.intern("frame_id"), pmt.PMT_NIL), pmt.from_long(77)))
                self.assertTrue(pmt.eqv(pmt.dict_ref(out_meta, pmt.intern("vcid"), pmt.PMT_NIL), pmt.from_long(5)))
                self.assertTrue(pmt.eqv(pmt.dict_ref(out_meta, pmt.intern("tag"), pmt.PMT_NIL), pmt.intern("A1")))
                self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("nonexistent_key")))
        finally:
            self._restore_pub(original_pub)

    # Additional: explicit protocol-id transition for empty/non-empty payloads
    def test_019_protocol_id_empty_vs_non_empty(self):
        original_pub = self._capture_pub()
        try:
            # Empty payload -> IDLE
            self.published = []
            msg_idle = self._make_pdu(self._default_meta(), b"")
            self.block.add_header(msg_idle)
            _, framed_idle = self._get_single_output()
            self.assertEqual((framed_idle[0] >> 2) & 0b111, 0b000)

            # Non-empty payload -> DATA
            self.published = []
            msg_data = self._make_pdu(self._default_meta(), bytes([0x01]))
            self.block.add_header(msg_data)
            _, framed_data = self._get_single_output()
            self.assertEqual((framed_data[0] >> 2) & 0b111, 0b111)
        finally:
            self._restore_pub(original_pub)

if __name__ == '__main__':
    gr_unittest.run(qa_encapsulation_header)
