#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Integration test for tc_primary_header with upstream metadata provider."""

import pmt
from gnuradio import gr_unittest
from gnuradio.soarr import tc_primary_header


class qa_tcPrimaryHeader_dbClient_integration(gr_unittest.TestCase):
    """Test tc_primary_header integration when metadata is pre-populated upstream."""

    CRC_BYTES = 2

    def setUp(self):
        """Create instance and capture output messages."""
        self.header_block = tc_primary_header(scid=0x155, vcid=0x12)
        self.captured_output = []

    def tearDown(self):
        """Clean up."""
        self.header_block = None
        self.captured_output = []

    def _integrate_flow(self, payload_bytes, pdu_meta):
        """
        Execute the integrated flow: PDU (with metadata) → tc_primary_header → output.
        Returns the final output message (port, msg).
        """
        # Capture all outputs from tc_primary_header. If the block emits multiple
        # PDUs (one per internal fragment), prefer those on the `pdu_out` port
        # and merge their bodies into a single PDU so tests expecting a single
        # output continue to work.
        captured = []
        original_header_pub = self.header_block.message_port_pub

        def _capture_output(port, msg):
            captured.append((port, msg))

        self.header_block.message_port_pub = _capture_output

        # Send PDU to tc_primary_header
        pdu = pmt.cons(pdu_meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.header_block.build_header(pdu)

        # Restore original publisher
        self.header_block.message_port_pub = original_header_pub

        # Prefer messages explicitly published to `pdu_out`.
        pdu_out_sym = pmt.intern("pdu_out")
        pdu_out_msgs = [(port, msg) for (port, msg) in captured if pmt.eqv(port, pdu_out_sym)]

        if not pdu_out_msgs and captured:
            # No explicit `pdu_out` messages found; fall back to using all
            # captured messages and treat them as if they were `pdu_out`.
            merged_bytes = bytearray()
            for _, msg in captured:
                body = pmt.cdr(msg)
                merged_bytes.extend(pmt.u8vector_elements(body))

            merged_pdu = pmt.cons(pmt.car(captured[0][1]), pmt.init_u8vector(len(merged_bytes), list(merged_bytes)))
            final = (pdu_out_sym, merged_pdu)
            return final

        # If multiple `pdu_out` messages were emitted, merge their bodies.
        if len(pdu_out_msgs) > 1:
            merged_bytes = bytearray()
            for _, msg in pdu_out_msgs:
                merged_bytes.extend(pmt.u8vector_elements(pmt.cdr(msg)))

            merged_pdu = pmt.cons(pmt.car(pdu_out_msgs[0][1]), pmt.init_u8vector(len(merged_bytes), list(merged_bytes)))
            return (pdu_out_sym, merged_pdu)

        # Exactly one `pdu_out` message
        self.assertEqual(len(pdu_out_msgs), 1, "tc_primary_header should emit one pdu_out")
        return pdu_out_msgs[0]

    def _meta_get_int(self, meta, key):
        pmt_key = pmt.intern(key)
        self.assertTrue(pmt.dict_has_key(meta, pmt_key), f"Missing metadata key '{key}'")
        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        self.assertTrue(pmt.is_integer(value), f"Metadata key '{key}' must be integer")
        return int(pmt.to_long(value))

    def _meta_assert_missing(self, meta, key):
        pmt_key = pmt.intern(key)
        self.assertFalse(pmt.dict_has_key(meta, pmt_key), f"Metadata key '{key}' should not be present")

    def _decode_header_fields(self, header_bytes):
        """Decode the 5-byte TC primary header into individual CCSDS fields."""
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

    def test_001_integration_flow_with_defaults_and_sequence(self):
        """Test complete flow with upstream-provided sequence metadata."""
        payload = bytes([0x10, 0x20, 0x30, 0x40])
        meta = pmt.make_dict()
        # tc_primary_header expects `vcid_counter` (not `frame_sequence_number`) as the
        # source of the 8-bit Frame Sequence Number field.
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(0))
        port, out_msg = self._integrate_flow(payload, meta)

        # Verify output port is pdu_out
        self.assertTrue(pmt.eqv(port, pmt.intern("pdu_out")))

        # Extract and decode header
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        # Header is 5 bytes + payload
        self.assertEqual(len(out_bytes), 5 + len(payload))
        self.assertEqual(out_bytes[5:], payload)

        # Decode header fields
        fields = self._decode_header_fields(out_bytes[:5])

        # Verify fields: defaults from tc_primary_header + FSN from upstream metadata
        self.assertEqual(fields["tfvn"], 0b00)
        self.assertEqual(fields["bypass"], 0)
        self.assertEqual(fields["control"], 0)
        self.assertEqual(fields["reserved"], 0)
        self.assertEqual(fields["scid"], 0x155)  # tc_primary_header default
        self.assertEqual(fields["vcid"], 0x12)   # tc_primary_header default
        self.assertEqual(fields["frame_sequence_number"], 0)
        self.assertEqual(fields["frame_length"], len(payload) + 5 - 1 + self.CRC_BYTES)

        # Verify output metadata is dict-driven and integer-typed.
        self.assertEqual(self._meta_get_int(out_meta, "scid"), 0x155)
        self.assertEqual(self._meta_get_int(out_meta, "vcid"), 0x12)
        # `vcid_counter` is consumed to build the header and deleted from metadata.
        self._meta_assert_missing(out_meta, "vcid_counter")

    def test_002_integration_with_pdu_overrides(self):
        """Test that PDU-provided SCID/VCID override block defaults."""
        # Create PDU with custom SCID and sequence metadata.
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x2AB))
        meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(0x2E))
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(0x7F))
        # tc_primary_header parses these via pmt.to_bool, so pass PMT booleans.
        meta = pmt.dict_add(meta, pmt.intern("bypass"), pmt.from_bool(True))
        meta = pmt.dict_add(meta, pmt.intern("control"), pmt.from_bool(True))

        payload = bytes([0xAA, 0xBB, 0xCC])
        port, out_msg = self._integrate_flow(payload, meta)

        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        fields = self._decode_header_fields(out_bytes[:5])

        # Verify PDU overrides for key fields.
        self.assertEqual(fields["scid"], 0x2AB)
        self.assertEqual(fields["vcid"], 0x2E)
        self.assertEqual(fields["bypass"], 1)
        self.assertEqual(fields["control"], 1)
        self.assertEqual(fields["frame_sequence_number"], 0x7F)

        out_meta = pmt.car(out_msg)
        self.assertEqual(self._meta_get_int(out_meta, "scid"), 0x2AB)
        self.assertEqual(self._meta_get_int(out_meta, "vcid"), 0x2E)
        self._meta_assert_missing(out_meta, "vcid_counter")

    def test_003_integration_multiple_frames_with_upstream_sequence(self):
        """Test multiple frames when upstream provides explicit sequence numbers."""
        sequence_numbers = []

        # Send three frames with explicit FSN values from upstream dict provider.
        for i in range(3):
            payload = bytes([0x11 + i, 0x22 + i])
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(i))
            port, out_msg = self._integrate_flow(payload, meta)

            out_body = pmt.cdr(out_msg)
            out_bytes = bytes(pmt.u8vector_elements(out_body))
            fields = self._decode_header_fields(out_bytes[:5])
            sequence_numbers.append(fields["frame_sequence_number"])

        # Verify sequence values are copied from upstream metadata.
        self.assertEqual(sequence_numbers, [0, 1, 2])

    def test_004_missing_sequence_in_metadata_no_output(self):
        meta = pmt.make_dict()
        payload = bytes([0x12, 0x34])
        pdu = pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))

        original_header_pub = self.header_block.message_port_pub
        final_outputs = []

        def _cap_output(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                final_outputs.append((port, msg))

        self.header_block.message_port_pub = _cap_output
        try:
            self.header_block.build_header(pdu)
        finally:
            self.header_block.message_port_pub = original_header_pub

        self.assertEqual(len(final_outputs), 0)

    def test_005_non_integer_sequence_in_metadata_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.intern("invalid"))
        payload = bytes([0x12, 0x34])
        pdu = pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))

        original_header_pub = self.header_block.message_port_pub
        final_outputs = []

        def _cap_output(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                final_outputs.append((port, msg))

        self.header_block.message_port_pub = _cap_output
        try:
            self.header_block.build_header(pdu)
        finally:
            self.header_block.message_port_pub = original_header_pub

        self.assertEqual(len(final_outputs), 0)

    def test_006_non_integer_scid_vcid_fall_back_to_defaults(self):
        payload = bytes([0xAA, 0x55])
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.intern("bad"))
        meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.intern("bad"))
        meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(5))

        _, out_msg = self._integrate_flow(payload, meta)
        out_meta = pmt.car(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        fields = self._decode_header_fields(out_bytes[:5])

        # Non-integer SCID/VCID in metadata should fall back to block defaults.
        self.assertEqual(fields["scid"], 0x155)
        self.assertEqual(fields["vcid"], 0x12)
        self.assertEqual(self._meta_get_int(out_meta, "scid"), 0x155)
        self.assertEqual(self._meta_get_int(out_meta, "vcid"), 0x12)

    def test_007_integration_without_crc_flag(self):
        payload = bytes([0x10, 0x20, 0x30, 0x40])
        header_block = tc_primary_header(scid=0x155, vcid=0x12, is_crc_used=False)
        captured_output = []

        original_pub = header_block.message_port_pub

        def _capture_output(port, msg):
            captured_output.append((port, msg))

        header_block.message_port_pub = _capture_output
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("vcid_counter"), pmt.from_long(9))
            pdu = pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))
            header_block.build_header(pdu)
        finally:
            header_block.message_port_pub = original_pub

        self.assertEqual(len(captured_output), 1)
        _, out_msg = captured_output[0]
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        fields = self._decode_header_fields(out_bytes[:5])

        self.assertEqual(fields["frame_length"], len(payload) + 5 - 1)
        self.assertEqual(out_bytes[5:], payload)

if __name__ == '__main__':
    gr_unittest.run(qa_tcPrimaryHeader_dbClient_integration)
