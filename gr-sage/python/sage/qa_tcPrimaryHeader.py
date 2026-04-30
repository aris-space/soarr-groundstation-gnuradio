#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.sage import tcPrimaryHeader


class qa_tcPrimaryHeader(gr_unittest.TestCase):
    """Unit tests for tcPrimaryHeader message flow and header encoding."""

    def setUp(self):
        # Default constructor values used when input metadata does not provide them.
        self.block = tcPrimaryHeader(scid=0x155, vcid=0x12)
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
        instance = tcPrimaryHeader()
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
        self.assertEqual(fields["frame_length"], len(payload_bytes) + 5 - 1)


if __name__ == '__main__':
    gr_unittest.run(qa_tcPrimaryHeader)
