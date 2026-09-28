#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import struct
import logging
import pmt

from gnuradio.soarr import cltu_framer

from gnuradio import gr_unittest

Log = logging.getLogger("qa_cltu_framer")

START = 0xEB90
END = 0xC5C5_C5C5_C5C5_C579


class qa_cltu_framer(gr_unittest.TestCase):

    def _make_pdu(self, payload, meta=None):
        if meta is None:
            meta = pmt.make_dict()
        return pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))

    def _run_and_capture(self, dut, pdu):
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            dut.add_sequences(pdu)
        finally:
            dut.message_port_pub = original_pub

        return captured

    def _assert_single_output_payload(self, captured, expected_payload):
        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        self.assertEqual(payload, expected_payload)

    def test_instance(self):
        Log.info("Testing CLTU Framer instantiation")
        dut = cltu_framer(start_sequence=START, tail_sequence=END)

        self.assertIsNotNone(dut)
        self.assertEqual(dut.start_sequence, START)
        self.assertEqual(dut.tail_sequence, END)
        self.assertEqual(dut.name(), "CLTU Framer")

    def test_001_functionality_check(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_dict = pmt.make_dict()
        input_dict = pmt.dict_add(input_dict, pmt.intern("test_key"), pmt.from_long(777))
        input_data = [i % 256 for i in range(1, 9)]
        input_pdu = self._make_pdu(input_data, input_dict)

        captured = self._run_and_capture(dut, input_pdu)

        start_bytes = struct.pack("!H", START)
        tail_bytes = struct.pack("!Q", END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self._assert_single_output_payload(captured, expected_payload)

        _, result = captured[0]
        output_dict = pmt.car(result)
        self.assertTrue(pmt.is_dict(output_dict))
        self.assertEqual(input_dict, output_dict)

    def test_002_empty_payload(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_pdu = self._make_pdu([])

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 0)

    def test_003_wrong_payload_size(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0x01, 0x02, 0x03]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 0)

    def test_004_non_u8vector_payload(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_pdu = pmt.cons(pmt.make_dict(), pmt.intern("This is not a u8vector"))

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 0)

    def test_005_custom_sequences(self):
        custom_start = 0x1234
        custom_end = 0xABCD_ABCD_ABCD_ABCD
        dut = cltu_framer(start_sequence=custom_start, tail_sequence=custom_end)

        input_data = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        start_bytes = struct.pack("!H", custom_start)
        tail_bytes = struct.pack("!Q", custom_end)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self._assert_single_output_payload(captured, expected_payload)

    def test_006_runtime_sequence_modification(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11]

        first_pdu = self._make_pdu(input_data)
        first_captured = self._run_and_capture(dut, first_pdu)

        new_start = 0x9999
        new_end = 0x7777_7777_7777_7777
        dut.start_sequence = new_start
        dut.tail_sequence = new_end

        second_pdu = self._make_pdu(input_data)
        second_captured = self._run_and_capture(dut, second_pdu)

        start_bytes_orig = struct.pack("!H", START)
        tail_bytes_orig = struct.pack("!Q", END)
        expected_1 = start_bytes_orig + bytes(input_data) + tail_bytes_orig
        self._assert_single_output_payload(first_captured, expected_1)

        start_bytes_new = struct.pack("!H", new_start)
        tail_bytes_new = struct.pack("!Q", new_end)
        expected_2 = start_bytes_new + bytes(input_data) + tail_bytes_new
        self._assert_single_output_payload(second_captured, expected_2)

    def test_007_multiple_consecutive_messages(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]

        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            for _ in range(5):
                dut.add_sequences(self._make_pdu(input_data))
        finally:
            dut.message_port_pub = original_pub

        self.assertEqual(len(captured), 5)

        start_bytes = struct.pack("!H", START)
        tail_bytes = struct.pack("!Q", END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        for _, msg in captured:
            payload = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            self.assertEqual(payload, expected_payload)

    def test_008_all_zero_payload(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        start_bytes = struct.pack("!H", START)
        tail_bytes = struct.pack("!Q", END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self._assert_single_output_payload(captured, expected_payload)

    def test_009_all_max_payload(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        start_bytes = struct.pack("!H", START)
        tail_bytes = struct.pack("!Q", END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self._assert_single_output_payload(captured, expected_payload)

    def test_010_empty_metadata_dict(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_dict = pmt.make_dict()
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]
        input_pdu = self._make_pdu(input_data, input_dict)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        output_dict = pmt.car(result)
        self.assertTrue(pmt.is_dict(output_dict))
        self.assertEqual(output_dict, input_dict)

    def test_011_rich_metadata_preservation(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)

        input_dict = pmt.make_dict()
        input_dict = pmt.dict_add(input_dict, pmt.intern("id"), pmt.from_long(123))
        input_dict = pmt.dict_add(input_dict, pmt.intern("timestamp"), pmt.from_double(1234567890.5))
        input_dict = pmt.dict_add(input_dict, pmt.intern("source"), pmt.intern("test_source"))

        input_data = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22]
        input_pdu = self._make_pdu(input_data, input_dict)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))

        start_bytes = struct.pack("!H", START)
        tail_bytes = struct.pack("!Q", END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes
        self.assertEqual(payload, expected_payload)

        output_dict = pmt.car(result)
        self.assertEqual(output_dict, input_dict)

    def test_012_payload_size_boundary_7_bytes(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 0)

    def test_013_payload_size_boundary_9_bytes(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 0)

    def test_014_default_sequences(self):
        dut = cltu_framer()
        input_data = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))

        default_start = 0xEB90
        default_end = 0xC5C5_C5C5_C5C5_C579

        start_bytes = struct.pack("!H", default_start)
        tail_bytes = struct.pack("!Q", default_end)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self.assertEqual(payload, expected_payload)
        self.assertEqual(dut.start_sequence, default_start)
        self.assertEqual(dut.tail_sequence, default_end)

    # Additional: start_sequence/tail_sequence are rejected at construction
    # time if they don't fit their packed width, matching every other
    # reviewed block's fail-fast-at-construction precedent.
    def test_015_invalid_sequences_raise_at_construction(self):
        with self.assertRaises(ValueError):
            cltu_framer(start_sequence=0x10000)
        with self.assertRaises(ValueError):
            cltu_framer(start_sequence=-1)
        with self.assertRaises(ValueError):
            cltu_framer(tail_sequence=0x10000000000000000)
        with self.assertRaises(ValueError):
            cltu_framer(tail_sequence=-1)

    # Additional: a non-pair input must not crash the handler - dropped
    # cleanly (logged, no publish) instead of pmt.car raising out of it.
    def test_016_non_pair_input_is_dropped_not_raised(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            dut.add_sequences(pmt.intern("not-a-pair"))  # must not raise
        finally:
            dut.message_port_pub = original_pub

        self.assertEqual(len(captured), 0)

    # Additional: start_sequence/tail_sequence are directly mutable at
    # runtime (test_006), which bypasses __init__'s validation entirely -
    # a bad reassigned value must be caught and dropped, not left to raise
    # struct.error out of the real message handler.
    def test_017_bad_runtime_reassigned_sequence_is_dropped_not_raised(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        dut.start_sequence = 0x10000  # out of range for struct '!H'

        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]
        input_pdu = self._make_pdu(input_data)

        captured = self._run_and_capture(dut, input_pdu)  # must not raise

        self.assertEqual(len(captured), 0)

    # CCSDS 231.0-B-4 Figure 5-1: one CLTU is one start sequence, all of a
    # frame's codewords, and one tail sequence - not one CLTU per codeword.
    def test_018_multi_codeword_payload_framed_as_one_cltu(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        input_data = list(range(24))  # 3 codewords
        captured = self._run_and_capture(dut, self._make_pdu(input_data))

        expected = struct.pack("!H", START) + bytes(input_data) + struct.pack("!Q", END)
        self._assert_single_output_payload(captured, expected)

    def test_019_payload_not_a_whole_number_of_codewords_is_rejected(self):
        dut = cltu_framer(start_sequence=START, tail_sequence=END)
        for size in (12, 17, 23):
            captured = self._run_and_capture(dut, self._make_pdu(list(range(size))))
            self.assertEqual(len(captured), 0, f"{size}-byte payload should be rejected")


if __name__ == "__main__":
    gr_unittest.run(qa_cltu_framer)
