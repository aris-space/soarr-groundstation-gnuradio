#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest, blocks
import pmt

from gnuradio.soarr import aqusitionIdleSequencer


class qa_aqusitionIdleSequencer(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def test_instance(self):
        instance = aqusitionIdleSequencer()
        self.assertTrue(instance is not None)

    def test_001_initial_acquisition(self):
        acq_seq = [0xBB, 0xBB, 0xBB, 0xBB]
        idle_seq = 0xAA
        instance = aqusitionIdleSequencer(idle_sequence=idle_seq, acquisition_sequence=acq_seq, initial_acquisition=True)

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
        instance = aqusitionIdleSequencer(idle_sequence=idle_seq, acquisition_sequence=acq_seq, initial_acquisition=True, diff_encoded=True)

        head = blocks.head(gr.sizeof_char, 6)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()

        out_data = snk.data()
        expected = [0xFF] * 6
        self.assertEqual(list(out_data), expected)

    def test_003_data_passthrough(self):
        idle_seq = 0xAA
        instance = aqusitionIdleSequencer(idle_sequence=idle_seq, initial_acquisition=False)

        # create a PDU (meta + u8vector payload) and publish to the block
        input_data = [1, 2, 3]
        meta = pmt.make_dict()
        payload = pmt.init_u8vector(len(input_data), input_data)
        pdu = pmt.cons(meta, payload)

        head = blocks.head(gr.sizeof_char, 5)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)

        # publish pdu and run
        instance.message_port_pub(pmt.intern("in"), pdu)
        self.tb.run()

        out_data = snk.data()
        expected = [1, 2, 3, idle_seq, idle_seq]
        self.assertEqual(list(out_data), expected)

    def test_004_idle_alternating_bits(self):
        # Verify idle byte unpacks to alternating bits 01010101 (0x55)
        idle_byte = 0x55
        instance = aqusitionIdleSequencer(idle_sequence=idle_byte, initial_acquisition=False)

        head = blocks.head(gr.sizeof_char, 8)
        snk = blocks.vector_sink_b()

        self.tb.connect(instance, head, snk)
        self.tb.run()

        out = list(snk.data())
        # every output byte should equal idle_byte
        self.assertTrue(all(b == idle_byte for b in out))

if __name__ == '__main__':
    gr_unittest.run(qa_aqusitionIdleSequencer)
