#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest, blocks
import pmt

from gnuradio.soarr import acquisition_idle_sequencer


class qa_acquisition_idle_sequencer(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def test_instance(self):
        instance = acquisition_idle_sequencer()
        self.assertTrue(instance is not None)

    def test_001_initial_acquisition(self):
        acq_seq = [0xBB, 0xBB, 0xBB, 0xBB]
        idle_seq = 0xAA
        instance = acquisition_idle_sequencer(idle_sequence=idle_seq, acquisition_sequence=acq_seq, initial_acquisition=True)

        head = blocks.head(gr.sizeof_char, 10)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()

        out_data = snk.data()
        expected = list(acq_seq) + [idle_seq] * 6
        self.assertEqual(list(out_data), expected)

    def test_002_diff_encoded_override(self):
        acq_seq = [0xBB, 0xBB, 0xBB, 0xBB]
        idle_seq = 0xAA
        instance = acquisition_idle_sequencer(idle_sequence=idle_seq, acquisition_sequence=acq_seq, initial_acquisition=True, diff_encoded=True)

        head = blocks.head(gr.sizeof_char, 6)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()

        out_data = snk.data()
        expected = [0xFF] * 6
        self.assertEqual(list(out_data), expected)

    def test_003_data_passthrough(self):
        idle_seq = 0xAA
        instance = acquisition_idle_sequencer(idle_sequence=idle_seq, initial_acquisition=False)

        # create a PDU (meta + u8vector payload) and publish to the block
        input_data = [1, 2, 3]
        meta = pmt.make_dict()
        payload = pmt.init_u8vector(len(input_data), input_data)
        pdu = pmt.cons(meta, payload)

        head = blocks.head(gr.sizeof_char, 5)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)

        # feed pdu directly into the handler and run
        instance.handle_msg(pdu)
        self.tb.run()

        out_data = snk.data()
        expected = [1, 2, 3, idle_seq, idle_seq]
        self.assertEqual(list(out_data), expected)

    def test_004_idle_alternating_bits(self):
        # Verify idle byte unpacks to alternating bits 01010101 (0x55)
        idle_byte = 0x55
        instance = acquisition_idle_sequencer(idle_sequence=idle_byte, initial_acquisition=False)

        head = blocks.head(gr.sizeof_char, 8)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()

        out = list(snk.data())
        # every output byte should equal idle_byte
        self.assertTrue(all(b == idle_byte for b in out))

    # Additional: idle_sequence/acquisition_sequence are rejected at
    # construction time if any value doesn't fit a single byte, matching
    # every other reviewed block's fail-fast-at-construction precedent.
    def test_005_invalid_idle_sequence_raises_at_construction(self):
        with self.assertRaises(ValueError):
            acquisition_idle_sequencer(idle_sequence=256)
        with self.assertRaises(ValueError):
            acquisition_idle_sequencer(idle_sequence=-1)

    def test_006_invalid_acquisition_sequence_raises_at_construction(self):
        with self.assertRaises(ValueError):
            acquisition_idle_sequencer(acquisition_sequence=[0xAA, 256])
        with self.assertRaises(ValueError):
            acquisition_idle_sequencer(acquisition_sequence=[-1])

    # Additional: a malformed `in` message (not a pair, or a non-u8vector
    # payload) is dropped cleanly - no crash, nothing queued.
    def test_007_malformed_message_is_dropped_not_queued(self):
        instance = acquisition_idle_sequencer()

        instance.handle_msg(pmt.intern("not-a-pair"))
        self.assertEqual(len(instance._pdu_queue), 0)

        bad_pdu = pmt.cons(pmt.make_dict(), pmt.intern("not-a-u8vector"))
        instance.handle_msg(bad_pdu)
        self.assertEqual(len(instance._pdu_queue), 0)

    # Additional: the default tsb_tag_name=None must not crash work() -
    # this is a regression guard for a previously reproduced native
    # access violation (add_item_tag called with a None tag key).
    def test_008_default_tsb_tag_name_does_not_crash(self):
        instance = acquisition_idle_sequencer(initial_acquisition=True)
        self.assertIsNone(instance.tsb_tag_key)

        head = blocks.head(gr.sizeof_char, 32)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()  # must not crash

        self.assertEqual(len(snk.data()), 32)


if __name__ == '__main__':
    gr_unittest.run(qa_acquisition_idle_sequencer)
