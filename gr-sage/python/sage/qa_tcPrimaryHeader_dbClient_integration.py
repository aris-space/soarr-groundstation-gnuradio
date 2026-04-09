#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Integration test for tcPrimaryHeader with upstream metadata provider."""

import pmt
from gnuradio import gr_unittest
from gnuradio.sage import tcPrimaryHeader


class qa_tcPrimaryHeader_dbClient_integration(gr_unittest.TestCase):
    """Test tcPrimaryHeader integration when metadata is pre-populated upstream."""

    def setUp(self):
        """Create instance and capture output messages."""
        self.header_block = tcPrimaryHeader(scid=0x155, vcid=0x12)
        self.captured_output = []

    def tearDown(self):
        """Clean up."""
        self.header_block = None
        self.captured_output = []

    def _integrate_flow(self, payload_bytes, pdu_meta):
        """
        Execute the integrated flow: PDU (with metadata) → tcPrimaryHeader → output.
        Returns the final output message (port, msg).
        """
        # Capture pdu_out from tcPrimaryHeader.
        final_outputs = []
        original_header_pub = self.header_block.message_port_pub

        def _capture_output(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                final_outputs.append((port, msg))

        self.header_block.message_port_pub = _capture_output

        # Send PDU to tcPrimaryHeader
        pdu = pmt.cons(pdu_meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.header_block.build_header(pdu)

        # Restore all
        self.header_block.message_port_pub = original_header_pub

        self.assertEqual(len(final_outputs), 1, "tcPrimaryHeader should emit one pdu_out")
        return final_outputs[0]

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
        meta = pmt.dict_add(meta, pmt.intern("frame_sequence_number"), pmt.from_long(0))
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

        # Verify fields: defaults from tcPrimaryHeader + FSN from upstream metadata
        self.assertEqual(fields["tfvn"], 0b00)
        self.assertEqual(fields["bypass"], 0)
        self.assertEqual(fields["control"], 0)
        self.assertEqual(fields["reserved"], 0)
        self.assertEqual(fields["scid"], 0x155)  # tcPrimaryHeader default
        self.assertEqual(fields["vcid"], 0x12)   # tcPrimaryHeader default
        self.assertEqual(fields["frame_sequence_number"], 0)
        self.assertEqual(fields["frame_length"], len(payload) + 5 - 1)

    def test_002_integration_with_pdu_overrides(self):
        """Test that PDU-provided SCID/VCID override block defaults."""
        # Create PDU with custom SCID and sequence metadata.
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x2AB))
        meta = pmt.dict_add(meta, pmt.intern("frame_sequence_number"), pmt.from_long(0x7F))
        meta = pmt.dict_add(meta, pmt.intern("bypass"), pmt.from_long(1))
        meta = pmt.dict_add(meta, pmt.intern("control"), pmt.from_long(1))

        payload = bytes([0xAA, 0xBB, 0xCC])
        port, out_msg = self._integrate_flow(payload, meta)

        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        fields = self._decode_header_fields(out_bytes[:5])

        # Verify PDU overrides (SCID overridden, VCID uses default 0x12)
        self.assertEqual(fields["scid"], 0x2AB)
        self.assertEqual(fields["vcid"], 0x12)  # Default from tcPrimaryHeader
        self.assertEqual(fields["bypass"], 1)
        self.assertEqual(fields["control"], 1)
        self.assertEqual(fields["frame_sequence_number"], 0x7F)

    def test_003_integration_multiple_frames_with_upstream_sequence(self):
        """Test multiple frames when upstream provides explicit sequence numbers."""
        sequence_numbers = []

        # Send three frames with explicit FSN values from upstream dict provider.
        for i in range(3):
            payload = bytes([0x11 + i, 0x22 + i])
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("frame_sequence_number"), pmt.from_long(i))
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


if __name__ == '__main__':
    gr_unittest.run(qa_tcPrimaryHeader_dbClient_integration)
