#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import numpy as np
import pmt

try:
    from gnuradio.soarr import data_creator
except ImportError:
    try:
        from .data_creator import data_creator
    except ImportError:
        from data_creator import data_creator

class qa_data_creator(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def _capture_specific_port(self, block, port_name, captured):
        """Capture messages published on one message port."""
        original_pub = block.message_port_pub

        def _capture(port, msg):
            if pmt.eqv(port, pmt.intern(port_name)):
                captured.append(msg)

        block.message_port_pub = _capture
        return original_pub

    def _restore_port(self, block, original_pub):
        """Restore original publisher after capture."""
        block.message_port_pub = original_pub

    def _pmt_get_int(self, meta, key):
        """Read an integer from PMT dictionary metadata."""
        value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
        self.assertFalse(pmt.eqv(value, pmt.PMT_NIL), f"Missing metadata key '{key}'")
        try:
            return int(pmt.to_long(value))
        except Exception:
            return int(pmt.to_uint64(value))

    def _pmt_get_nested_int(self, meta, path):
        current = meta
        for key in path:
            current = pmt.dict_ref(current, pmt.intern(key), pmt.PMT_NIL)
            self.assertFalse(pmt.eqv(current, pmt.PMT_NIL), f"Missing metadata key '{key}'")
        try:
            return int(pmt.to_long(current))
        except Exception:
            return int(pmt.to_uint64(current))

    def test_instance(self):
        instance = data_creator(data_length_bytes=1)
        self.assertIsNotNone(instance)
        self.assertEqual(instance.mode, 0)
        self.assertEqual(instance.scid, 0)
        self.assertEqual(instance.spi, 0)
        self.assertEqual(instance.bypass, False)
        self.assertEqual(instance.control, False)

    def test_001_generate_message_with_manual_data(self):
        payload = np.array([0x10, 0x20, 0x30, 0x40], dtype=np.uint8)
        instance = data_creator(mode=0, data=payload, data_length_bytes=None, scid=341, spi=7)

        captured = []
        original_pub = self._capture_specific_port(instance, "out", captured)
        try:
            instance.generate_message(pmt.PMT_NIL)
        finally:
            self._restore_port(instance, original_pub)

        self.assertEqual(len(captured), 1)
        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)

        self.assertEqual(self._pmt_get_nested_int(out_meta, ["telecommand", "tc_header", "scid"]), 341)
        self.assertEqual(self._pmt_get_nested_int(out_meta, ["sdls", "security_header", "spi"]), 7)
        self.assertTrue(pmt.is_u8vector(out_body))
        self.assertEqual(bytes(pmt.u8vector_elements(out_body)), bytes([0x10, 0x20, 0x30, 0x40]))

    def test_002_choose_mode_generates_random_payload_with_length(self):
        expected_length = 16
        instance = data_creator(mode=0, data=None, data_length_bytes=expected_length, scid=1, spi=2)

        captured = []
        original_pub = self._capture_specific_port(instance, "out", captured)
        try:
            instance._choose_mode(pmt.PMT_NIL)
        finally:
            self._restore_port(instance, original_pub)

        self.assertEqual(len(captured), 1)
        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertEqual(self._pmt_get_nested_int(out_meta, ["telecommand", "tc_header", "scid"]), 1)
        self.assertEqual(self._pmt_get_nested_int(out_meta, ["sdls", "security_header", "spi"]), 2)
        self.assertEqual(len(out_bytes), expected_length)

    def test_003_raises_when_data_and_length_both_provided(self):
        payload = np.array([0x01, 0x02], dtype=np.uint8)
        with self.assertRaises(ValueError):
            data_creator(mode=0, data=payload, data_length_bytes=2, scid=0, spi=0)

    def test_004_raises_for_unsupported_mode(self):
        instance = data_creator(mode=99, data_length_bytes=4)
        with self.assertRaises(NotImplementedError):
            instance._choose_mode(pmt.PMT_NIL)

    def test_005_message_ports_registered(self):
        instance = data_creator(data_length_bytes=1)
        self.assertIsNotNone(instance.message_ports_in())
        self.assertIsNotNone(instance.message_ports_out())

    def test_006_bypass_flag_default_false(self):
        instance = data_creator(data_length_bytes=1)
        self.assertEqual(instance.bypass, False)

    def test_007_bypass_flag_set_true(self):
        instance = data_creator(data_length_bytes=1, bypass=True)
        self.assertEqual(instance.bypass, True)

    def test_008_control_flag_default_false(self):
        instance = data_creator(data_length_bytes=1)
        self.assertEqual(instance.control, False)

    def test_009_control_flag_set_true(self):
        instance = data_creator(data_length_bytes=1, control=True)
        self.assertEqual(instance.control, True)

    def test_010_both_flags_set(self):
        instance = data_creator(data_length_bytes=1, bypass=True, control=True)
        self.assertEqual(instance.bypass, True)
        self.assertEqual(instance.control, True)

    def test_011_flags_with_other_parameters(self):
        payload = np.array([0x10, 0x20], dtype=np.uint8)
        instance = data_creator(mode=0, data=payload, scid=100, spi=50, bypass=True, control=False)
        self.assertEqual(instance.bypass, True)
        self.assertEqual(instance.control, False)
        self.assertEqual(instance.scid, 100)
        self.assertEqual(instance.spi, 50)


if __name__ == '__main__':
    gr_unittest.run(qa_data_creator)
