#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import struct
import pmt
from gnuradio import gr_unittest

from gnuradio.soarr import cltu_deframer

START = 0xEB90
TAIL = 0xC5C5_C5C5_C5C5_C579


class qa_cltu_deframer(gr_unittest.TestCase):

    def _make_frame(self, payload, start_sequence=START, tail_sequence=TAIL):
        return struct.pack("!H", start_sequence) + bytes(payload) + struct.pack("!Q", tail_sequence)

    def _run_and_capture(self, dut, chunk):
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            dut.process_bytes(chunk)
        finally:
            dut.message_port_pub = original_pub

        return captured

    def _run_and_capture_tagged_bits(self, dut, bit_stream, tag_offset):
        captured = []
        original_pub = dut.message_port_pub

        def _capture(port, msg):
            captured.append((port, msg))

        dut.message_port_pub = _capture
        try:
            dut.test_set_bit_buffer(bit_stream, 0)
            dut.test_set_pending_tag(tag_offset)
            dut.process_pending_tag()
        finally:
            dut.message_port_pub = original_pub

        return captured

    def test_instance(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        self.assertIsNotNone(dut)
        self.assertEqual(dut.start_sequence, START)
        self.assertEqual(dut.tail_sequence, TAIL)
        self.assertEqual(dut.data_length, 8)
        self.assertEqual(dut.name(), "cltu_deframer")
        self.assertEqual(dut.input_packed, True)
        self.assertEqual(dut.output_packed, True)

    def test_001_extract_single_frame(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        chunk = self._make_frame(payload)

        captured = self._run_and_capture(dut, chunk)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        self.assertTrue(pmt.is_dict(pmt.car(result)))
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_002_extract_frame_after_noise(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        payload = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11]
        chunk = bytes([0x00, 0x11, 0x22]) + self._make_frame(payload) + bytes([0x33, 0x44])

        captured = self._run_and_capture(dut, chunk)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_003_handles_split_input(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        payload = [0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF]
        frame = self._make_frame(payload)

        first = self._run_and_capture(dut, frame[:7])
        second = self._run_and_capture(dut, frame[7:])

        self.assertEqual(len(first), 0)
        self.assertEqual(len(second), 1)
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(second[0][1]))), bytes(payload))

    def test_004_multiple_frames(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        first_payload = [0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80]
        second_payload = [0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88]
        chunk = self._make_frame(first_payload) + self._make_frame(second_payload)

        captured = self._run_and_capture(dut, chunk)

        self.assertEqual(len(captured), 2)
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(captured[0][1]))), bytes(first_payload))
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(captured[1][1]))), bytes(second_payload))

    def _bytes_to_bits(self, data):
        bits = []
        for value in data:
            for shift in range(7, -1, -1):
                bits.append((value >> shift) & 1)
        return bytes(bits)

    def test_005_input_unpacked_bits(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, input_packed=False)
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        frame = self._make_frame(payload)

        # convert full frame to bits (MSB-first per byte)
        bit_stream = self._bytes_to_bits(frame)

        captured = self._run_and_capture(dut, bit_stream)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_006_output_unpacked_bits(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, output_packed=False)
        payload = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11]
        frame = self._make_frame(payload)

        captured = self._run_and_capture(dut, frame)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        published = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        expected_bits = self._bytes_to_bits(bytes(payload))
        self.assertEqual(published, expected_bits)

    def test_007_correlate_access_code_tag(self):
        """Simulate a Correlate Access Code - Tag and verify a PDU is produced."""
        dut = cltu_deframer(
            start_sequence=START,
            tail_sequence=TAIL,
            input_packed=False,
            output_packed=True,
            tag_name="start",
        )
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        frame = self._make_frame(payload)
        bit_stream = self._bytes_to_bits(frame)

        # Correlator tags the last bit of the access code window (index 15)
        class _Tag:
            def __init__(self, offset, key):
                self.offset = offset
                self.key = key

        tag = _Tag(offset=15, key=pmt.intern("start"))

        captured = self._run_and_capture_tagged_bits(dut, bit_stream, tag.offset)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_010_tag_requires_full_frame(self):
        """Ensure a tag does not emit a PDU until the full frame is buffered."""
        dut = cltu_deframer(
            start_sequence=START,
            tail_sequence=TAIL,
            input_packed=False,
            output_packed=True,
            tag_name="start",
        )
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        frame = self._make_frame(payload)
        bit_stream = self._bytes_to_bits(frame)

        class _Tag:
            def __init__(self, offset, key):
                self.offset = offset
                self.key = key

        tag = _Tag(offset=15, key=pmt.intern("start"))

        original_pub = dut.message_port_pub
        try:
            first = []
            dut.message_port_pub = lambda port, msg: first.append((port, msg))
            dut.test_set_bit_buffer(bit_stream[:40], 0)
            dut.test_set_pending_tag(tag.offset)
            dut.process_pending_tag()

            second = []
            dut.message_port_pub = lambda port, msg: second.append((port, msg))
            dut.test_set_bit_buffer(bit_stream, 0)
            dut.process_pending_tag()
        finally:
            dut.message_port_pub = original_pub

        self.assertEqual(len(first), 0)
        self.assertEqual(len(second), 1)
        _, result = second[0]
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_011_threshold_allows_start_errors(self):
        """Allow a small number of start-code bit errors within threshold."""
        dut = cltu_deframer(
            start_sequence=START,
            tail_sequence=TAIL,
            input_packed=False,
            output_packed=True,
            tag_name="start",
            threshold=2,
        )
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        frame = self._make_frame(payload)
        bit_stream = list(self._bytes_to_bits(frame))

        # Flip two bits within the access code window (first 16 bits)
        bit_stream[0] ^= 1
        bit_stream[5] ^= 1
        bit_stream = bytes(bit_stream)

        class _Tag:
            def __init__(self, offset, key):
                self.offset = offset
                self.key = key

        tag = _Tag(offset=15, key=pmt.intern("start"))

        captured = self._run_and_capture_tagged_bits(dut, bit_stream, tag.offset)

        self.assertEqual(len(captured), 1)
        _, result = captured[0]
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(result))), bytes(payload))

    def test_012_threshold_rejects_too_many_errors(self):
        """Reject frames when start-code errors exceed threshold."""
        dut = cltu_deframer(
            start_sequence=START,
            tail_sequence=TAIL,
            input_packed=False,
            output_packed=True,
            tag_name="start",
            threshold=2,
        )
        payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
        frame = self._make_frame(payload)
        bit_stream = list(self._bytes_to_bits(frame))

        # Flip three bits within the access code window (first 16 bits)
        bit_stream[0] ^= 1
        bit_stream[5] ^= 1
        bit_stream[10] ^= 1
        bit_stream = bytes(bit_stream)

        class _Tag:
            def __init__(self, offset, key):
                self.offset = offset
                self.key = key

        tag = _Tag(offset=15, key=pmt.intern("start"))

        captured = self._run_and_capture_tagged_bits(dut, bit_stream, tag.offset)

        self.assertEqual(len(captured), 0)

    # --- CLTUs with more than one codeword (CCSDS 231.0-B-4 Figure 5-1:
    # start sequence + N codewords + tail sequence) ---

    CODEWORDS = [
        [0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17],
        [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27],
        [0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37],
    ]

    def _make_cltu(self, codewords):
        return self._make_frame([b for cw in codewords for b in cw])

    def _payloads(self, captured):
        return [bytes(pmt.u8vector_elements(pmt.cdr(msg))) for _, msg in captured]

    def _scramble_resets(self, captured):
        return [pmt.to_bool(pmt.dict_ref(pmt.car(msg), pmt.intern("scramble_reset"), pmt.PMT_NIL))
                for _, msg in captured]

    def test_013_multi_codeword_cltu_is_split_into_codewords(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        captured = self._run_and_capture(dut, self._make_cltu(self.CODEWORDS))

        self.assertEqual(self._payloads(captured), [bytes(cw) for cw in self.CODEWORDS])
        # Only a CLTU's first codeword restarts the de-randomizer
        self.assertEqual(self._scramble_resets(captured), [True, False, False])

    def test_014_multi_codeword_cltu_waits_for_tail(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        cltu = self._make_cltu(self.CODEWORDS)

        first = self._run_and_capture(dut, cltu[:2 + 16])  # start + 2 codewords
        second = self._run_and_capture(dut, cltu[2 + 16:])

        self.assertEqual(len(first), 0)
        self.assertEqual(self._payloads(second), [bytes(cw) for cw in self.CODEWORDS])

    def test_015_multi_codeword_cltu_unpacked_bits(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, input_packed=False)
        bit_stream = self._bytes_to_bits(bytes([0x42]) + self._make_cltu(self.CODEWORDS))

        captured = self._run_and_capture(dut, bit_stream)

        self.assertEqual(self._payloads(captured), [bytes(cw) for cw in self.CODEWORDS])

    def test_016_back_to_back_cltus_of_different_lengths(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        chunk = (self._make_cltu(self.CODEWORDS[:2]) + bytes([0x00, 0x00])
                 + self._make_cltu(self.CODEWORDS))

        captured = self._run_and_capture(dut, chunk)

        expected = [bytes(cw) for cw in self.CODEWORDS[:2] + self.CODEWORDS]
        self.assertEqual(self._payloads(captured), expected)
        self.assertEqual(self._scramble_resets(captured), [True, False, True, False, False])

    def test_017_tagged_multi_codeword_cltu(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, input_packed=False,
                            output_packed=True, tag_name="start")
        bit_stream = self._bytes_to_bits(self._make_cltu(self.CODEWORDS))

        original_pub = dut.message_port_pub
        try:
            first = []
            dut.message_port_pub = lambda port, msg: first.append((port, msg))
            dut.test_set_bit_buffer(bit_stream[:16 + 2 * 64], 0)  # tail not yet received
            dut.test_set_pending_tag(15)
            dut.process_pending_tag()

            second = []
            dut.message_port_pub = lambda port, msg: second.append((port, msg))
            dut.test_set_bit_buffer(bit_stream, 0)
            dut.process_pending_tag()
        finally:
            dut.message_port_pub = original_pub

        self.assertEqual(len(first), 0)
        self.assertEqual(self._payloads(second), [bytes(cw) for cw in self.CODEWORDS])

    def test_018_missing_tail_is_discarded_after_max_codewords(self):
        # A start sequence with no tail within max_codewords codewords is not
        # a CLTU; the search resumes after it and finds the real one.
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, max_codewords=3)
        chunk = struct.pack("!H", START) + bytes(8 * 4) + self._make_cltu(self.CODEWORDS[:1])

        captured = self._run_and_capture(dut, chunk)

        self.assertEqual(self._payloads(captured), [bytes(self.CODEWORDS[0])])

    def test_019_cltu_longer_than_max_codewords_is_rejected(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, max_codewords=2)
        captured = self._run_and_capture(dut, self._make_cltu(self.CODEWORDS))
        self.assertEqual(len(captured), 0)

    def test_020_start_directly_followed_by_tail_is_ignored(self):
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL)
        chunk = self._make_cltu([]) + self._make_cltu(self.CODEWORDS[:1])

        captured = self._run_and_capture(dut, chunk)

        self.assertEqual(self._payloads(captured), [bytes(self.CODEWORDS[0])])

    def test_021_invalid_max_codewords_raises(self):
        with self.assertRaises(ValueError):
            cltu_deframer(max_codewords=0)

    def test_022_stream_with_real_tags_through_general_work(self):
        # The path GNU Radio's scheduler actually uses: a running flowgraph
        # feeding unpacked bits with "start" tags (as correlate_access_code
        # _tag_bb places them, on the access code's last bit). The spurious
        # tag inside the first CLTU must not produce anything.
        import time
        from gnuradio import gr, blocks

        noise = bytes([0x00, 0x00, 0x00])
        first = self._make_cltu(self.CODEWORDS[:2])
        second = self._make_cltu(self.CODEWORDS[2:])
        stream = noise + first + noise + second + noise
        bits = list(self._bytes_to_bits(stream))

        def _tag(offset):
            tag = gr.tag_t()
            tag.offset = offset
            tag.key = pmt.intern("start")
            tag.value = pmt.PMT_T
            tag.srcid = pmt.PMT_F
            return tag

        first_start = len(noise) * 8
        second_start = (len(noise) + len(first) + len(noise)) * 8
        tags = [_tag(first_start + 15), _tag(first_start + 16 + 20), _tag(second_start + 15)]

        tb = gr.top_block()
        src = blocks.vector_source_b(bits, False, 1, tags)
        dut = cltu_deframer(start_sequence=START, tail_sequence=TAIL, input_packed=False,
                            output_packed=True, tag_name="start")
        sink = blocks.message_debug()
        tb.connect(src, dut)
        tb.msg_connect(dut, "out", sink, "store")

        tb.start()
        deadline = time.time() + 5
        while sink.num_messages() < 3 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)  # let any unexpected extra message arrive
        tb.stop()
        tb.wait()

        payloads = [bytes(pmt.u8vector_elements(pmt.cdr(sink.get_message(i))))
                    for i in range(sink.num_messages())]
        self.assertEqual(payloads, [bytes(cw) for cw in self.CODEWORDS])


if __name__ == '__main__':
    gr_unittest.run(qa_cltu_deframer)
