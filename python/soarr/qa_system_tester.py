#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
import pmt

try:
    from gnuradio.soarr import system_tester
except ImportError:
    try:
        from .system_tester import system_tester
    except ImportError:
        from system_tester import system_tester

class qa_system_tester(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def _capture_port(self, block, port_name, captured):
        original_pub = block.message_port_pub

        def _capture(port, msg):
            if pmt.eqv(port, pmt.intern(port_name)):
                captured.append(msg)

        block.message_port_pub = _capture
        return original_pub

    def _restore_port(self, block, original_pub):
        block.message_port_pub = original_pub

    def _make_pdu(self, payload_bytes, vcid_counter=0):
        tc_header = pmt.make_dict()
        tc_header = pmt.dict_add(tc_header, pmt.intern("vcid_counter"), pmt.from_long(vcid_counter))
        telecommand = pmt.make_dict()
        telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("telecommand"), telecommand)
        return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

    def test_instance(self):
        instance = system_tester()
        self.assertIsNotNone(instance)
        self.assertIsNotNone(instance.message_ports_in())
        self.assertIsNotNone(instance.message_ports_out())
        self.assertEqual(instance.mode, 0)

    def test_001_waiting_mode_triggers_and_matches_payload(self):
        instance = system_tester(timeout_s=1.0, stats_path=None)
        captured = []
        original_pub = self._capture_port(instance, "trigger", captured)

        try:
            instance.handle_start(pmt.PMT_NIL)
        finally:
            self._restore_port(instance, original_pub)

        self.assertEqual(len(captured), 1)
        trigger_msg = captured[0]
        trigger_meta = pmt.car(trigger_msg)
        self.assertTrue(pmt.dict_has_key(trigger_meta, pmt.intern("packet_id")))

        payload = bytes([0x10, 0x20, 0x30, 0x40])
        instance.handle_original(self._make_pdu(payload, vcid_counter=0))
        instance.handle_received(self._make_pdu(payload, vcid_counter=0))

        stats = instance.get_stats()
        self.assertEqual(stats["generated_packets"], 1)
        self.assertEqual(stats["received_packets"], 1)
        self.assertEqual(stats["lost_packets"], 0)
        self.assertEqual(stats["message_errors"], 0)
        self.assertEqual(stats["bit_errors"], 0)

    def test_002_payload_difference_counts_as_message_and_bit_error(self):
        instance = system_tester(timeout_s=1.0, stats_path=None)
        instance.handle_start(pmt.PMT_NIL)

        reference = bytes([0x00, 0xFF])
        received = bytes([0x01, 0xFF])

        instance.handle_original(self._make_pdu(reference, vcid_counter=0))
        instance.handle_received(self._make_pdu(received, vcid_counter=0))

        stats = instance.get_stats()
        self.assertEqual(stats["received_packets"], 1)
        self.assertEqual(stats["message_errors"], 1)
        self.assertEqual(stats["lost_packets"], 0)
        self.assertGreater(stats["bit_errors"], 0)

    def test_003_timeout_marks_packet_lost(self):
        instance = system_tester(timeout_s=1.0, stats_path=None)
        captured = []
        original_pub = self._capture_port(instance, "trigger", captured)

        try:
            instance.handle_start(pmt.PMT_NIL)
        finally:
            self._restore_port(instance, original_pub)

        trigger_meta = pmt.car(captured[0])
        packet_id = int(pmt.to_long(pmt.dict_ref(trigger_meta, pmt.intern("packet_id"), pmt.PMT_NIL)))
        instance._timeout_packet(packet_id)

        stats = instance.get_stats()
        self.assertEqual(stats["lost_packets"], 1)
        self.assertEqual(stats["received_packets"], 0)

    def test_004_save_stats_failure_does_not_crash_handle_received(self):
        instance = system_tester(timeout_s=1.0, stats_path=None)
        instance.handle_start(pmt.PMT_NIL)
        payload = bytes([1, 2, 3])
        instance.handle_original(self._make_pdu(payload, vcid_counter=0))

        def _raise_on_save():
            raise OSError("forced save failure")

        instance._save_stats = _raise_on_save

        # Should not raise, even though _finalize_entry's _update_stats
        # call will hit the forced failure.
        instance.handle_received(self._make_pdu(payload, vcid_counter=0))

    def test_005_trigger_publish_failure_does_not_crash_handle_start(self):
        instance = system_tester(timeout_s=1.0, stats_path=None)

        def _raise_on_publish(port, msg):
            raise RuntimeError("forced publish failure")

        instance.message_port_pub = _raise_on_publish

        # Should not raise.
        instance.handle_start(pmt.PMT_NIL)


if __name__ == '__main__':
    gr_unittest.run(qa_system_tester)
