#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from unittest.mock import patch

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import tc_primary_header


class qa_tc_primary_header(gr_unittest.TestCase):
    """Unit tests for tc_primary_header message flow and header encoding."""

    CRC_BYTES = 2

    def setUp(self):
        # Default constructor values used when input metadata does not provide them.
        self.block = tc_primary_header(scid=0x155, vcid=0x12)
        self.published = []

    def tearDown(self):
        self.block = None
        self.published = []

    def _capture_pub(self):
        # Replace message_port_pub with a capture hook so tests can inspect outputs.
        original_pub = self.block.message_port_pub

        def _capture(port, msg):
            self.published.append((port, msg))

        self.block.message_port_pub = _capture
        return original_pub

    def _restore_pub(self, original_pub):
        self.block.message_port_pub = original_pub

    def _decode_header_fields(self, header_bytes):
        # Decode the 5-byte TC primary header into individual CCSDS fields.
        value = int.from_bytes(header_bytes, byteorder="big")
        return {
            "tfvn": (value >> 38) & 0x3,
            "bypass": (value >> 37) & 0x1,
            "control": (value >> 36) & 0x1,
            "reserved": (value >> 34) & 0x3,
            "scid": (value >> 24) & 0x3FF,
            "vcid": (value >> 18) & 0x3F,
            "frame_length": (value >> 8) & 0x3FF,
            "frame_sequence_number": value & 0xFF,
        }

    def test_instance(self):
        # Smoke test: block can be instantiated with default arguments.
        instance = tc_primary_header()
        self.assertIsNotNone(instance)

    def test_001_crc_disabled_can_be_instantiated(self):
        # The new constructor flag should allow CRC-free header construction.
        instance = tc_primary_header(scid=0x155, vcid=0x12, is_crc_used=False)
        self.assertIsNotNone(instance)

    def test_001_missing_vcid_counter_emits_no_output(self):
        meta = pmt.make_dict()
        payload = pmt.init_u8vector(3, [1, 2, 3])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.build_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_002_defaults_used_when_scid_vcid_missing(self):
        # If PDU metadata has no SCID/VCID, constructor defaults must be used.
        payload_bytes = bytes([0x10, 0x20, 0x30, 0x40])
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(0x2A))
        msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

        original_pub = self._capture_pub()
        try:
            self.block.build_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        fields = self._decode_header_fields(out_bytes[:5])

        self.assertEqual(fields["scid"], 0x155)
        self.assertEqual(fields["vcid"], 0x12)
        self.assertEqual(fields["frame_sequence_number"], 0x2A)

    def test_003_header_build_with_pdu_fields(self):
        # Header build should prioritize PDU-provided SCID/VCID/flags/sequence.
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x2AB))
        meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(0x2E))
        meta = pmt.dict_add(meta, pmt.intern("bypass"), pmt.from_bool(True))
        meta = pmt.dict_add(meta, pmt.intern("control"), pmt.from_bool(False))
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(0x7F))

        payload_bytes = bytes([0xAA, 0xBB, 0xCC])
        msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

        original_pub = self._capture_pub()
        try:
            self.block.build_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_bytes[5:], payload_bytes)

        fields = self._decode_header_fields(out_bytes[:5])
        self.assertEqual(fields["tfvn"], 0b00)
        self.assertEqual(fields["bypass"], 1)
        self.assertEqual(fields["control"], 0)
        self.assertEqual(fields["reserved"], 0)
        self.assertEqual(fields["scid"], 0x2AB)
        self.assertEqual(fields["vcid"], 0x2E)
        self.assertEqual(fields["frame_sequence_number"], 0x7F)
        self.assertEqual(fields["frame_length"], len(payload_bytes) + 5 - 1 + self.CRC_BYTES)

    def test_004_frame_length_without_crc_flag(self):
        # When CRC is disabled, the frame length should not include the extra bytes.
        block = tc_primary_header(scid=0x155, vcid=0x12, is_crc_used=False)
        published = []

        original_pub = block.message_port_pub

        def _capture(port, msg):
            published.append((port, msg))

        block.message_port_pub = _capture
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(0x3C))
            payload_bytes = bytes([0xAA, 0xBB, 0xCC])
            msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
            block.build_header(msg)
        finally:
            block.message_port_pub = original_pub

        self.assertEqual(len(published), 1)
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(published[0][1])))
        fields = self._decode_header_fields(out_bytes[:5])
        self.assertEqual(fields["frame_length"], len(payload_bytes) + 5 - 1)

    # Additional: scid/vcid are rejected at construction time if they
    # don't fit their field's bit width, matching the GRC block.yml's own
    # asserts range, instead of being silently accepted and only failing
    # later when packed (or not caught at all, since scid/vcid were never
    # masked before packing).
    def test_005_scid_vcid_out_of_range_raise_at_construction(self):
        with self.assertRaises(ValueError):
            tc_primary_header(scid=0x400, vcid=0x12)
        with self.assertRaises(ValueError):
            tc_primary_header(scid=-1, vcid=0x12)
        with self.assertRaises(ValueError):
            tc_primary_header(scid=0x155, vcid=0x40)
        with self.assertRaises(ValueError):
            tc_primary_header(scid=0x155, vcid=-1)

    # Additional: a frame_length present in metadata but not convertible
    # to an integer is dropped cleanly (logged, no publish) instead of
    # leaving frame_length as None and crashing later on `None & mask`.
    def test_006_unconvertible_frame_length_is_dropped_not_raised(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(1))
        meta = pmt.dict_add(meta, pmt.intern("frame_length"), pmt.intern("not-an-int"))
        payload = pmt.init_u8vector(2, [1, 2])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.build_header(msg)  # must not raise
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # Additional: an internal failure past field extraction (e.g. a
    # future encoding-library incompatibility) is caught, logged, and
    # dropped - not left to raise out of the real message handler.
    def test_007_internal_build_failure_is_dropped_not_raised(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(1))
        payload = pmt.init_u8vector(2, [1, 2])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            with patch(
                "gnuradio.soarr.tc_primary_header.TC_PRIMARY_HEADER_STRUCT.build",
                side_effect=RuntimeError("simulated build failure"),
            ):
                self.block.build_header(msg)  # must not raise
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    # Additional: a computed frame_length that doesn't fit the 10-bit
    # field is rejected (dropped, logged) instead of silently wrapping
    # via bitmasking into a different, wrong value with no diagnostic.
    def test_008_oversized_frame_length_is_dropped_not_wrapped(self):
        block = tc_primary_header(scid=0x155, vcid=0x12, is_crc_used=False)
        published = []

        original_pub = block.message_port_pub

        def _capture(port, msg):
            published.append((port, msg))

        block.message_port_pub = _capture
        try:
            # payload_len + 5 (header) - 1 = 1024, one past the 10-bit
            # field's 1023 max - would previously wrap to 0 via masking.
            payload_bytes = bytes(1020)
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(1))
            msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
            block.build_header(msg)
        finally:
            block.message_port_pub = original_pub

        self.assertEqual(len(published), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_tc_primary_header)
