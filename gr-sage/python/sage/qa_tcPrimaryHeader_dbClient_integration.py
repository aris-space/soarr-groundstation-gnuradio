#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Integration test for tcPrimaryHeader and dbClient working together."""

import pmt
from gnuradio import gr_unittest
from gnuradio.sage import tcPrimaryHeader, dbClient


class qa_tcPrimaryHeader_dbClient_integration(gr_unittest.TestCase):
    """Test tcPrimaryHeader and dbClient message flow integration."""

    def setUp(self):
        """Create instances and capture message flows."""
        self.header_block = tcPrimaryHeader(scid=0x155, vcid=0x12)
        self.db_block = dbClient(type=0)
        self.captured_output = []

    def tearDown(self):
        """Clean up."""
        self.header_block = None
        self.db_block = None
        self.captured_output = []

    def _integrate_flow(self, payload_bytes, pdu_meta=None):
        """
        Execute the integrated flow: PDU → tcPrimaryHeader → dbClient → tcPrimaryHeader → output.
        Returns the final output message (port, msg).
        """
        if pdu_meta is None:
            pdu_meta = pmt.make_dict()

        # Capture query from tcPrimaryHeader
        db_query_messages = []
        original_header_pub = self.header_block.message_port_pub

        def _capture_query(port, msg):
            if pmt.eqv(port, pmt.intern("tc_query")):
                db_query_messages.append(msg)

        self.header_block.message_port_pub = _capture_query

        # Send PDU to tcPrimaryHeader
        pdu = pmt.cons(pdu_meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.header_block.call_db(pdu)

        # Restore header pub and verify we got a query
        self.header_block.message_port_pub = original_header_pub
        self.assertEqual(len(db_query_messages), 1, "tcPrimaryHeader should emit one tc_query")

        # Feed query to dbClient and capture response
        db_responses = []
        original_db_pub = self.db_block.message_port_pub

        def _capture_response(port, msg):
            if pmt.eqv(port, pmt.intern("tc_callback")):
                db_responses.append(msg)

        self.db_block.message_port_pub = _capture_response
        self.db_block.make_tc_call(db_query_messages[0])

        # Restore db pub and verify response
        self.db_block.message_port_pub = original_db_pub
        self.assertEqual(len(db_responses), 1, "dbClient should emit one tc_callback")

        # Feed response back to tcPrimaryHeader and capture output
        final_outputs = []
        original_header_pub = self.header_block.message_port_pub

        def _capture_output(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                final_outputs.append((port, msg))

        self.header_block.message_port_pub = _capture_output
        self.header_block.build_header(db_responses[0])

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

    def test_001_integration_flow_with_defaults(self):
        """Test complete flow: PDU in → tcPrimaryHeader → db query → db callback → header out."""
        payload = bytes([0x10, 0x20, 0x30, 0x40])
        port, out_msg = self._integrate_flow(payload)

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

        # Verify fields: defaults from tcPrimaryHeader + FSN from dbClient
        self.assertEqual(fields["tfvn"], 0b00)
        self.assertEqual(fields["bypass"], 0)
        self.assertEqual(fields["control"], 0)
        self.assertEqual(fields["reserved"], 0)
        self.assertEqual(fields["scid"], 0x155)  # tcPrimaryHeader default
        self.assertEqual(fields["vcid"], 0x12)   # tcPrimaryHeader default
        self.assertEqual(fields["frame_sequence_number"], 0)  # First from dummy db
        self.assertEqual(fields["frame_length"], len(payload) + 5 - 1)

    def test_002_integration_with_pdu_overrides(self):
        """Test that PDU-provided SCID/VCID override block defaults."""
        # Create PDU with custom SCID (but keep VCID=0x12 which exists in dummy db)
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x2AB))
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

    def test_003_integration_multiple_frames_increments_sequence(self):
        """Test that multiple frames use incrementing sequence numbers from dbClient."""
        sequence_numbers = []

        # Send three frames, tracking their sequence numbers
        for i in range(3):
            payload = bytes([0x11 + i, 0x22 + i])
            port, out_msg = self._integrate_flow(payload)

            out_body = pmt.cdr(out_msg)
            out_bytes = bytes(pmt.u8vector_elements(out_body))
            fields = self._decode_header_fields(out_bytes[:5])
            sequence_numbers.append(fields["frame_sequence_number"])

        # Verify incrementing sequence from dummy db
        self.assertEqual(sequence_numbers, [0, 1, 2])

    def test_004_integration_with_yaml_db(self):
        """Test integration with YAML-backed dbClient."""
        import tempfile
        from pathlib import Path

        yaml_content = """
entries:
  "46":
    SCID: 683
    SPI: 7
    VCID: 46
    encryption_key: "AAAA"
    authentication_key: "BBBB"
    sdls_counter: 0
    vcid_counter: 100
"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            db_yaml = dbClient(type=1, yaml_path=str(yaml_path))
            header = tcPrimaryHeader(scid=0x155, vcid=0x2E)

            # Capture query phase
            db_queries = []
            original_header_pub = header.message_port_pub

            def _cap_query(port, msg):
                if pmt.eqv(port, pmt.intern("tc_query")):
                    db_queries.append(msg)

            header.message_port_pub = _cap_query

            # Send PDU with VCID and SCID matching YAML, override block defaults
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(46))
            meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(683))
            payload = bytes([0x12, 0x34])
            pdu = pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))
            header.call_db(pdu)

            header.message_port_pub = original_header_pub
            self.assertEqual(len(db_queries), 1)

            # Capture response phase
            db_responses = []
            original_db_pub = db_yaml.message_port_pub

            def _cap_response(port, msg):
                if pmt.eqv(port, pmt.intern("tc_callback")):
                    db_responses.append(msg)

            db_yaml.message_port_pub = _cap_response
            db_yaml.make_tc_call(db_queries[0])

            db_yaml.message_port_pub = original_db_pub
            self.assertEqual(len(db_responses), 1)

            # Final output phase
            final_outputs = []
            original_header_pub = header.message_port_pub

            def _cap_output(port, msg):
                if pmt.eqv(port, pmt.intern("pdu_out")):
                    final_outputs.append((port, msg))

            header.message_port_pub = _cap_output
            header.build_header(db_responses[0])

            header.message_port_pub = original_header_pub
            self.assertEqual(len(final_outputs), 1)

            port, out_msg = final_outputs[0]
            self.assertTrue(pmt.eqv(port, pmt.intern("pdu_out")))

            out_body = pmt.cdr(out_msg)
            out_bytes = bytes(pmt.u8vector_elements(out_body))

            fields = self._decode_header_fields(out_bytes[:5])

            # Verify YAML db data appears in header and counter from YAML (100) is used
            self.assertEqual(fields["scid"], 683)
            self.assertEqual(fields["vcid"], 46)
            # Sequence should be the vcid_counter from YAML (100)
            self.assertEqual(fields["frame_sequence_number"], 100 & 0xFF)


if __name__ == '__main__':
    gr_unittest.run(qa_tcPrimaryHeader_dbClient_integration)
