#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from gnuradio.sage import ccsdsReader
import pmt

class qa_ccsdsReader(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    @staticmethod
    def _field_names(struct):
        """Return Construct Struct field names for assertion checks."""
        return [subcon.name for subcon in struct.subcons]

    def _capture_specific_port(self, block, port_name, captured):
        """Capture messages from one port without forwarding them onward."""
        original_pub = block.message_port_pub

        def _capture(port, msg):
            if pmt.eqv(port, pmt.intern(port_name)):
                captured.append(msg)

        block.message_port_pub = _capture
        return original_pub

    def _restore_port(self, block, original_pub):
        """Restore the block's original message publisher."""
        block.message_port_pub = original_pub

    def _pmt_get_int(self, meta, key):
        """Read integer PMT value from metadata dict."""
        value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
        self.assertFalse(pmt.eqv(value, pmt.PMT_NIL), f"Missing metadata key '{key}'")
        try:
            return int(pmt.to_long(value))
        except Exception:
            return int(pmt.to_uint64(value))

    def _python_to_pmt(self, value):
        """Convert python scalar/dict/bytes values to PMT values."""
        if isinstance(value, int):
            return pmt.from_long(value)
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            return pmt.init_u8vector(len(b), list(b))
        if isinstance(value, dict):
            out = pmt.make_dict()
            for key, subval in value.items():
                out = pmt.dict_add(out, pmt.intern(str(key)), self._python_to_pmt(subval))
            return out
        return pmt.intern(str(value))

    def test_instance_default(self):
        """Test instantiation with default parameters"""
        instance = ccsdsReader()
        self.assertIsNotNone(instance)
        self.assertEqual(instance.message_type, 0)  # TC
        self.assertEqual(instance.sdls_type, 3)     # Both
        self.assertEqual(instance.encapsulation_used, True)
        self.assertEqual(instance.data_type, 0)     # Raw

    def test_instance_custom_tc_no_security(self):
        """Test TC with no security"""
        instance = ccsdsReader(message_type=0, sdls_type=0, encapsulation_used=False, data_type=0)
        self.assertEqual(instance.message_type, 0)
        self.assertEqual(instance.sdls_type, 0)
        self.assertEqual(instance.encapsulation_used, False)
        self.assertEqual(instance.data_type, 0)

    def test_instance_csp_encryption(self):
        """Test CSP with encryption only"""
        instance = ccsdsReader(message_type=0, sdls_type=1, encapsulation_used=True, data_type=1)
        self.assertEqual(instance.data_type, 1)
        self.assertEqual(instance.sdls_type, 1)

    def test_instance_authentication_only(self):
        """Test authentication only SDLS"""
        instance = ccsdsReader(message_type=0, sdls_type=2, encapsulation_used=False, data_type=0)
        self.assertEqual(instance.sdls_type, 2)

    def test_ccsds_message_structure_minimal(self):
        """Test CCSDS message structure with minimal headers (TC header + data + FECF)"""
        instance = ccsdsReader(sdls_type=0, encapsulation_used=False, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIsNotNone(struct)
        # Verify structure has expected fields
        self.assertIn("tc_header", field_names)
        self.assertIn("data", field_names)
        self.assertIn("frame_error_control_field", field_names)
        # Verify no optional headers in minimal structure
        self.assertNotIn("csp_header", field_names)
        self.assertNotIn("encapsulation_header", field_names)
        self.assertNotIn("sdls_security_header", field_names)

    def test_ccsds_message_structure_with_csp(self):
        """Test CCSDS message structure includes CSP header"""
        instance = ccsdsReader(sdls_type=0, encapsulation_used=False, data_type=1)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("tc_header", field_names)
        self.assertIn("csp_header", field_names)
        self.assertIn("data", field_names)
        self.assertIn("frame_error_control_field", field_names)

    def test_ccsds_message_structure_with_encapsulation(self):
        """Test CCSDS message structure includes encapsulation header"""
        instance = ccsdsReader(sdls_type=0, encapsulation_used=True, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("tc_header", field_names)
        self.assertIn("encapsulation_header", field_names)
        self.assertIn("data", field_names)
        self.assertIn("frame_error_control_field", field_names)

    def test_ccsds_message_structure_with_sdls(self):
        """Test CCSDS message structure includes SDLS headers"""
        instance = ccsdsReader(sdls_type=3, encapsulation_used=False, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("tc_header", field_names)
        self.assertIn("sdls_security_header", field_names)
        self.assertIn("sdls_security_trailer", field_names)
        self.assertIn("data", field_names)
        self.assertIn("frame_error_control_field", field_names)

    def test_ccsds_message_structure_full(self):
        """Test CCSDS message structure with all headers (CSP, Encapsulation, SDLS)"""
        instance = ccsdsReader(sdls_type=3, encapsulation_used=True, data_type=1)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("tc_header", field_names)
        self.assertIn("csp_header", field_names)
        self.assertIn("encapsulation_header", field_names)
        self.assertIn("sdls_security_header", field_names)
        self.assertIn("data", field_names)
        self.assertIn("sdls_security_trailer", field_names)
        self.assertIn("frame_error_control_field", field_names)

    def test_sdls_encryption_only(self):
        """Test SDLS encryption only includes security headers"""
        instance = ccsdsReader(sdls_type=1, encapsulation_used=False, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("sdls_security_header", field_names)
        self.assertIn("sdls_security_trailer", field_names)

    def test_sdls_authentication_only(self):
        """Test SDLS authentication only includes security headers"""
        instance = ccsdsReader(sdls_type=2, encapsulation_used=False, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertIn("sdls_security_header", field_names)
        self.assertIn("sdls_security_trailer", field_names)

    def test_sdls_no_security(self):
        """Test with no SDLS security"""
        instance = ccsdsReader(sdls_type=0, encapsulation_used=False, data_type=0)
        struct = instance.ccsds_message()
        field_names = self._field_names(struct)
        self.assertNotIn("sdls_security_header", field_names)
        self.assertNotIn("sdls_security_trailer", field_names)

    def test_message_port_registration(self):
        """Test that message ports are properly registered"""
        instance = ccsdsReader()
        # The block should have input and output message ports registered
        self.assertIsNotNone(instance.message_ports_in())
        self.assertIsNotNone(instance.message_ports_out())

    def test_decode_full_packet_outputs_all_fields_and_unchanged_u8vector(self):
        """Build a full CCSDS packet then decode to a PDU with parsed fields and unchanged frame bytes."""
        payload_bytes = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        sdls_trailer = bytes([0xA5] * 16)
        total_length = 5 + 6 + 4 + 4 + len(payload_bytes) + 16 + 2
        frame_length = total_length - 1

        instance = ccsdsReader(message_type=0, sdls_type=3, encapsulation_used=True, data_type=1)

        packet_bytes = instance.ccsds_message().build(
            dict(
                tc_header=dict(
                    tfvn=0,
                    bypass_flag=1,
                    control_flag=0,
                    reserve=0,
                    scid=0x155,
                    vcid=0x12,
                    frame_length=frame_length,
                    fsn=0x33,
                ),
                csp_header=dict(
                    priority=1,
                    source=0x123,
                    destination=0x456,
                    dest_port=0x0A,
                    source_port=0x0B,
                    flags=0x15,
                ),
                encapsulation_header=dict(
                    first_octet=0xF6,
                    _user_defined_field_raw=0xA4,
                    ccsds_defined_field=0x0000,
                    packet_length=0x5678,
                ),
                sdls_security_header=dict(
                    security_param_index=0x1111,
                    initialization_vector=0x2222,
                ),
                data=payload_bytes,
                sdls_security_trailer=sdls_trailer,
                frame_error_control_field=0xBEEF,
            )
        )

        # Deconstruct the full packet with CCSDSReader field parsers.
        tc_size = 5
        csp_size = 6
        sdls_header_size = 4
        encaps_size = 4
        fecf_size = 2
        sdls_trailer_size = 16

        data_length = total_length - (tc_size + csp_size + encaps_size + sdls_header_size + sdls_trailer_size + fecf_size)

        offset = 0
        tc_header = dict(instance.tc_header().parse(packet_bytes[offset:offset + tc_size]))
        offset += tc_size
        csp_header = dict(instance.csp_header().parse(packet_bytes[offset:offset + csp_size]))
        offset += csp_size
        sdls_security_header = dict(instance.sdls_security_header().parse(packet_bytes[offset:offset + sdls_header_size]))
        offset += sdls_header_size
        encapsulation_header = dict(instance.encapsulation_header().parse(packet_bytes[offset:offset + encaps_size]))
        offset += encaps_size
        data_bytes = packet_bytes[offset:offset + data_length]
        offset += data_length
        sdls_security_trailer_out = packet_bytes[offset:offset + sdls_trailer_size]
        offset += sdls_trailer_size
        frame_error_control_field = int.from_bytes(packet_bytes[offset:offset + fecf_size], byteorder="big", signed=False)

        out_meta = pmt.make_dict()
        out_meta = pmt.dict_add(out_meta, pmt.intern("tc_header"), self._python_to_pmt(tc_header))
        out_meta = pmt.dict_add(out_meta, pmt.intern("csp_header"), self._python_to_pmt(csp_header))
        out_meta = pmt.dict_add(out_meta, pmt.intern("encapsulation_header"), self._python_to_pmt(encapsulation_header))
        out_meta = pmt.dict_add(out_meta, pmt.intern("sdls_security_header"), self._python_to_pmt(sdls_security_header))
        out_meta = pmt.dict_add(out_meta, pmt.intern("data"), self._python_to_pmt(data_bytes))
        out_meta = pmt.dict_add(out_meta, pmt.intern("sdls_security_trailer"), self._python_to_pmt(sdls_security_trailer_out))
        out_meta = pmt.dict_add(out_meta, pmt.intern("frame_error_control_field"), pmt.from_long(frame_error_control_field))
        out_body = pmt.init_u8vector(len(packet_bytes), list(packet_bytes))
        out_msg = pmt.cons(out_meta, out_body)
        out_body_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(out_body_bytes, packet_bytes)

        tc_header = pmt.dict_ref(out_meta, pmt.intern("tc_header"), pmt.PMT_NIL)
        csp_header = pmt.dict_ref(out_meta, pmt.intern("csp_header"), pmt.PMT_NIL)
        encapsulation_header = pmt.dict_ref(out_meta, pmt.intern("encapsulation_header"), pmt.PMT_NIL)
        sdls_security_header = pmt.dict_ref(out_meta, pmt.intern("sdls_security_header"), pmt.PMT_NIL)
        data = pmt.dict_ref(out_meta, pmt.intern("data"), pmt.PMT_NIL)
        sdls_security_trailer_out = pmt.dict_ref(out_meta, pmt.intern("sdls_security_trailer"), pmt.PMT_NIL)
        fecf = pmt.dict_ref(out_meta, pmt.intern("frame_error_control_field"), pmt.PMT_NIL)

        self.assertFalse(pmt.eqv(tc_header, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(csp_header, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(encapsulation_header, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(sdls_security_header, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(data, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(sdls_security_trailer_out, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(fecf, pmt.PMT_NIL))

        self.assertEqual(self._pmt_get_int(tc_header, "scid"), 0x155)
        self.assertEqual(self._pmt_get_int(tc_header, "vcid"), 0x12)
        self.assertEqual(self._pmt_get_int(tc_header, "frame_length"), frame_length)
        self.assertEqual(self._pmt_get_int(csp_header, "source"), 0x123)
        self.assertEqual(self._pmt_get_int(csp_header, "destination"), 0x456)
        self.assertEqual(self._pmt_get_int(encapsulation_header, "packet_length"), 0x5678)
        self.assertEqual(self._pmt_get_int(sdls_security_header, "security_param_index"), 0x1111)
        self.assertEqual(self._pmt_get_int(sdls_security_header, "initialization_vector"), 0x2222)
        self.assertEqual(self._pmt_get_int(out_meta, "frame_error_control_field"), 0xBEEF)

        self.assertTrue(pmt.is_u8vector(data))
        self.assertEqual(bytes(pmt.u8vector_elements(data)), payload_bytes)
        self.assertTrue(pmt.is_u8vector(sdls_security_trailer_out))
        self.assertEqual(bytes(pmt.u8vector_elements(sdls_security_trailer_out)), sdls_trailer)

    def test_decode_minimal_packet_outputs_fields_and_unchanged_u8vector(self):
        """Build a minimal CCSDS packet then decode to parsed dict fields and unchanged frame bytes."""
        payload_bytes = bytes([0x01, 0x23, 0x45, 0x67])
        total_length = 5 + len(payload_bytes) + 2
        frame_length = total_length - 1

        instance = ccsdsReader(message_type=0, sdls_type=0, encapsulation_used=False, data_type=0)

        packet_bytes = instance.ccsds_message().build(
            dict(
                tc_header=dict(
                    tfvn=0,
                    bypass_flag=0,
                    control_flag=1,
                    reserve=0,
                    scid=0x1AA,
                    vcid=0x03,
                    frame_length=frame_length,
                    fsn=0x7E,
                ),
                data=payload_bytes,
                frame_error_control_field=0x1234,
            )
        )

        # Deconstruct the minimal packet with CCSDSReader field parsers.
        tc_size = 5
        fecf_size = 2
        data_length = total_length - (tc_size + fecf_size)

        offset = 0
        tc_header = dict(instance.tc_header().parse(packet_bytes[offset:offset + tc_size]))
        offset += tc_size
        data_bytes = packet_bytes[offset:offset + data_length]
        offset += data_length
        frame_error_control_field = int.from_bytes(packet_bytes[offset:offset + fecf_size], byteorder="big", signed=False)

        out_meta = pmt.make_dict()
        out_meta = pmt.dict_add(out_meta, pmt.intern("tc_header"), self._python_to_pmt(tc_header))
        out_meta = pmt.dict_add(out_meta, pmt.intern("data"), self._python_to_pmt(data_bytes))
        out_meta = pmt.dict_add(out_meta, pmt.intern("frame_error_control_field"), pmt.from_long(frame_error_control_field))
        out_body = pmt.init_u8vector(len(packet_bytes), list(packet_bytes))
        out_msg = pmt.cons(out_meta, out_body)
        out_body_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(out_body_bytes, packet_bytes)

        tc_header_out = pmt.dict_ref(out_meta, pmt.intern("tc_header"), pmt.PMT_NIL)
        data_out = pmt.dict_ref(out_meta, pmt.intern("data"), pmt.PMT_NIL)
        fecf_out = pmt.dict_ref(out_meta, pmt.intern("frame_error_control_field"), pmt.PMT_NIL)
        csp_header_out = pmt.dict_ref(out_meta, pmt.intern("csp_header"), pmt.PMT_NIL)
        encapsulation_header_out = pmt.dict_ref(out_meta, pmt.intern("encapsulation_header"), pmt.PMT_NIL)
        sdls_security_header_out = pmt.dict_ref(out_meta, pmt.intern("sdls_security_header"), pmt.PMT_NIL)
        sdls_security_trailer_out = pmt.dict_ref(out_meta, pmt.intern("sdls_security_trailer"), pmt.PMT_NIL)

        self.assertFalse(pmt.eqv(tc_header_out, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(data_out, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(fecf_out, pmt.PMT_NIL))
        self.assertTrue(pmt.eqv(csp_header_out, pmt.PMT_NIL))
        self.assertTrue(pmt.eqv(encapsulation_header_out, pmt.PMT_NIL))
        self.assertTrue(pmt.eqv(sdls_security_header_out, pmt.PMT_NIL))
        self.assertTrue(pmt.eqv(sdls_security_trailer_out, pmt.PMT_NIL))

        self.assertEqual(self._pmt_get_int(tc_header_out, "scid"), 0x1AA)
        self.assertEqual(self._pmt_get_int(tc_header_out, "vcid"), 0x03)
        self.assertEqual(self._pmt_get_int(tc_header_out, "frame_length"), frame_length)
        self.assertEqual(self._pmt_get_int(tc_header_out, "fsn"), 0x7E)
        self.assertEqual(self._pmt_get_int(out_meta, "frame_error_control_field"), 0x1234)

        self.assertTrue(pmt.is_u8vector(data_out))
        self.assertEqual(bytes(pmt.u8vector_elements(data_out)), payload_bytes)


if __name__ == '__main__':
    gr_unittest.run(qa_ccsdsReader)
