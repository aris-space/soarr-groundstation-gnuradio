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

    def test_003_mismatched_data_and_length_raises(self):
        # Explicit bytes/array data disagreeing with data_length_bytes is
        # rejected outright - there's no single correct way to reconcile
        # them (pad? truncate? both are guesses).
        payload = np.array([0x01, 0x02], dtype=np.uint8)
        with self.assertRaises(ValueError):
            data_creator(mode=0, data=payload, data_length_bytes=5, scid=0, spi=0)

    def test_004_unsupported_mode_is_dropped_not_raised(self):
        instance = data_creator(mode=99, data_length_bytes=4)
        captured = []
        original_pub = self._capture_specific_port(instance, "out", captured)
        try:
            instance._choose_mode(pmt.PMT_NIL)  # must not raise
        finally:
            self._restore_port(instance, original_pub)

        self.assertEqual(len(captured), 0)

    # Additional: neither data nor data_length_bytes provided raises a
    # clear ValueError, not an unrelated TypeError from numpy.
    def test_012_neither_data_nor_length_raises_value_error(self):
        with self.assertRaises(ValueError):
            data_creator()

    # Additional: an int data value combined with data_length_bytes is
    # allowed - the one case where the combination is unambiguous - and
    # left-pads exactly as the class docstring documents.
    def test_013_int_data_with_length_pads_correctly(self):
        instance = data_creator(data=0x00010203, data_length_bytes=4)
        self.assertEqual(bytes(instance.data), bytes([0x00, 0x01, 0x02, 0x03]))

    # Additional: explicit data whose length matches data_length_bytes is
    # accepted (not just rejected because both were given).
    def test_014_matching_data_and_length_succeeds(self):
        payload = np.array([0x01, 0x02], dtype=np.uint8)
        instance = data_creator(mode=0, data=payload, data_length_bytes=2, scid=0, spi=0)
        self.assertEqual(bytes(instance.data), bytes([0x01, 0x02]))

    # Additional: vcid/vcid_counter/sdls_counter are accepted parameters
    # that must actually end up in the published metadata.
    def test_015_vcid_vcid_counter_sdls_counter_in_output(self):
        instance = data_creator(
            mode=0,
            data=np.array([0xAA], dtype=np.uint8),
            scid=1,
            spi=2,
            vcid=3,
            vcid_counter=4,
            sdls_counter=5,
        )
        captured = []
        original_pub = self._capture_specific_port(instance, "out", captured)
        try:
            instance.generate_message(pmt.PMT_NIL)
        finally:
            self._restore_port(instance, original_pub)

        self.assertEqual(len(captured), 1)
        out_meta = pmt.car(captured[0])
        self.assertEqual(self._pmt_get_nested_int(out_meta, ["telecommand", "tc_header", "vcid"]), 3)
        self.assertEqual(self._pmt_get_nested_int(out_meta, ["telecommand", "tc_header", "vcid_counter"]), 4)
        self.assertEqual(self._pmt_get_nested_int(out_meta, ["sdls", "security_header", "sdls_counter"]), 5)

    # Additional: an internal failure past field extraction (e.g. publish
    # itself raising) is caught, logged, and dropped - not left to raise
    # out of the real message handler.
    def test_016_internal_publish_failure_is_dropped_not_raised(self):
        instance = data_creator(data=np.array([0x01], dtype=np.uint8))

        def _raise(port, out_msg):
            raise RuntimeError("simulated publish failure")

        instance.message_port_pub = _raise
        instance.generate_message(pmt.PMT_NIL)  # must not raise

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
