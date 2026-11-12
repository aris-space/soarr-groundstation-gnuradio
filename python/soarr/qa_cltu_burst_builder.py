#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time

from gnuradio import blocks, gr, gr_unittest, pdu
import pmt

from gnuradio.soarr import cltu_burst_builder, tm_cadu_framer, tm_frame_generator

BURST_MORE = pmt.intern("burst_more")
CLTU = bytes.fromhex("eb90") + bytes(range(16)) + bytes.fromhex("c5c5c5c5c5c5c579")
ACQ = 64  # default acquisition_length


class qa_cltu_burst_builder(gr_unittest.TestCase):

    def _run(self, block, msg):
        published = []
        original = block.message_port_pub
        block.message_port_pub = lambda port, out: published.append((port, out))
        try:
            block.build_burst(msg)
        finally:
            block.message_port_pub = original
        return published

    def _pdu(self, data, meta=None):
        meta = meta if meta is not None else pmt.make_dict()
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _marked(self, data, more, **fields):
        meta = pmt.dict_add(pmt.make_dict(), BURST_MORE, pmt.from_bool(more))
        for key, value in fields.items():
            meta = pmt.dict_add(meta, pmt.intern(key), pmt.from_long(value))
        return self._pdu(data, meta)

    def _bytes(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def test_instance(self):
        self.assertIsNotNone(cltu_burst_builder())

    def test_001_burst_is_acquisition_cltu_tail(self):
        published = self._run(cltu_burst_builder(), self._pdu(CLTU))
        self.assertEqual(len(published), 1)
        port, msg = published[0]
        self.assertTrue(pmt.eqv(port, pmt.intern("out")))
        # Defaults: ACQ acquisition bytes of alternating bits, 4 idle tail bytes
        self.assertEqual(self._bytes(msg), bytes([0xAA] * ACQ) + CLTU + bytes([0xAA] * 4))

    def test_002_lengths_are_configurable(self):
        block = cltu_burst_builder(acquisition_length=32, tail_length=0)
        (_, msg), = self._run(block, self._pdu(CLTU))
        self.assertEqual(self._bytes(msg), bytes([0xAA] * 32) + CLTU)
        block = cltu_burst_builder(acquisition_length=0, tail_length=8)
        (_, msg), = self._run(block, self._pdu(CLTU))
        self.assertEqual(self._bytes(msg), CLTU + bytes([0xAA] * 8))

    def test_003_diff_encoded_uses_all_ones(self):
        # A differential modulator turns 0xFF into alternating symbols
        (_, msg), = self._run(cltu_burst_builder(diff_encoded=True), self._pdu(CLTU))
        self.assertEqual(self._bytes(msg), bytes([0xFF] * ACQ) + CLTU + bytes([0xFF] * 4))

    def test_004_metadata_passed_through(self):
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("frame_id"), pmt.from_long(3))
        (_, msg), = self._run(cltu_burst_builder(), self._pdu(CLTU, meta))
        self.assertTrue(pmt.equal(pmt.car(msg), meta))

    def test_005_invalid_parameters_raise(self):
        for kwargs in ({"acquisition_length": -1}, {"tail_length": -1}, {"fill_byte": 256}, {"max_burst_pdus": 0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    cltu_burst_builder(**kwargs)

    def test_006_malformed_input_dropped_not_raised(self):
        block = cltu_burst_builder()
        self.assertEqual(self._run(block, pmt.intern("not-a-pair")), [])
        self.assertEqual(self._run(block, pmt.cons(pmt.make_dict(), pmt.intern("x"))), [])
        self.assertEqual(self._run(block, self._pdu(b"")), [])

    def test_007_internal_publish_failure_is_dropped_not_raised(self):
        block = cltu_burst_builder()

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        block.message_port_pub = _raise
        block.build_burst(self._pdu(CLTU))  # must not raise

    def test_008_stream_carries_only_bursts(self):
        # With GNU Radio's pdu_to_tagged_stream the stream holds exactly the
        # bursts, each starting with a length tag - no idle fill in between,
        # so nothing queues up ahead of the next CLTU.
        tb = gr.top_block()
        builder = cltu_burst_builder()
        to_stream = pdu.pdu_to_tagged_stream(gr.types.byte_t, "packet_len")
        sink = blocks.vector_sink_b()
        tb.msg_connect(builder, "out", to_stream, "pdus")
        tb.connect(to_stream, sink)
        tb.start()
        for _ in range(3):
            builder.to_basic_block()._post(pmt.intern("in"), self._pdu(CLTU))
        burst = bytes([0xAA] * ACQ) + CLTU + bytes([0xAA] * 4)
        deadline = time.time() + 5
        while len(sink.data()) < 3 * len(burst) and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        tb.stop()
        tb.wait()

        self.assertEqual(bytes(sink.data()), burst * 3)
        tags = [(t.offset, pmt.to_long(t.value)) for t in sink.tags() if pmt.symbol_to_string(t.key) == "packet_len"]
        self.assertEqual(tags, [(0, len(burst)), (len(burst), len(burst)), (2 * len(burst), len(burst))])

    # PDUs marked burst_more = True are held; the next PDU without the
    # marker closes the burst: one acquisition sequence, all PDUs back to
    # back (no bits between CADUs, CCSDS 131.0-B-5 9.4.2), one tail.
    def test_009_burst_more_joins_pdus_into_one_burst(self):
        block = cltu_burst_builder(acquisition_length=3, tail_length=2, fill_byte=0x55)
        self.assertEqual(self._run(block, self._marked(b"" * 4, True)), [])
        self.assertEqual(self._run(block, self._marked(b"" * 4, True)), [])
        (_, msg), = self._run(block, self._marked(b"" * 4, False))
        self.assertEqual(self._bytes(msg), b"U" * 3 + b"" * 4 + b"" * 4 + b"" * 4 + b"U" * 2)
        (_, msg), = self._run(block, self._pdu(b""))  # the next burst starts fresh
        self.assertEqual(self._bytes(msg), b"U" * 3 + b"" + b"U" * 2)
        self._run(block, self._marked(b"", True))
        (_, msg), = self._run(block, self._pdu(b""))  # no marker closes the burst as well
        self.assertEqual(self._bytes(msg), b"U" * 3 + b"" + b"U" * 2)

    # The burst gets the closing PDU's metadata, without the marker.
    def test_010_burst_metadata_is_the_last_pdus(self):
        block = cltu_burst_builder()
        self._run(block, self._marked(b"", True, frame=1))
        (_, msg), = self._run(block, self._marked(b"", False, frame=2))
        expected = pmt.dict_add(pmt.make_dict(), pmt.intern("frame"), pmt.from_long(2))
        self.assertTrue(pmt.equal(pmt.car(msg), expected))

    # A marker that never clears doesn't hold PDUs forever: after
    # max_burst_pdus they go out as a burst, the rest in the next one.
    def test_011_max_burst_pdus_sends_a_burst(self):
        block = cltu_burst_builder(acquisition_length=1, tail_length=1, fill_byte=0x55, max_burst_pdus=2)
        self.assertEqual(self._run(block, self._marked(b"", True)), [])
        (_, first), = self._run(block, self._marked(b"", True))
        self.assertEqual(self._bytes(first), b"UU")
        self.assertEqual(self._run(block, self._marked(b"", True)), [])
        (_, second), = self._run(block, self._marked(b"", False))
        self.assertEqual(self._bytes(second), b"UU")
        (_, single), = self._run(cltu_burst_builder(acquisition_length=0, tail_length=0, max_burst_pdus=1),
                                 self._marked(b"", True))
        self.assertEqual(self._bytes(single), b"")

    # A malformed PDU in the middle of a burst is dropped; the held PDUs
    # stay and the burst still closes.
    def test_012_malformed_pdu_keeps_the_held_burst(self):
        block = cltu_burst_builder(acquisition_length=0, tail_length=0)
        self._run(block, self._marked(b"", True))
        self.assertEqual(self._run(block, pmt.intern("not-a-pair")), [])
        self.assertEqual(self._run(block, self._marked(b"", False)), [])
        (_, msg), = self._run(block, self._pdu(b""))
        self.assertEqual(self._bytes(msg), b"")

    # A failed publish loses that burst only; the next starts fresh.
    def test_013_publish_failure_clears_the_held_burst(self):
        block = cltu_burst_builder(acquisition_length=0, tail_length=0)
        self._run(block, self._marked(b"", True))

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        original = block.message_port_pub
        block.message_port_pub = _raise
        block.build_burst(self._pdu(b""))  # must not raise
        block.message_port_pub = original
        (_, msg), = self._run(block, self._pdu(b""))
        self.assertEqual(self._bytes(msg), b"")

    # The TM chain: a packet split into three frames leaves the CADU framer
    # as three CADUs and goes out as one burst.
    def test_014_tm_frames_of_one_packet_are_one_burst(self):
        generator, framer = tm_frame_generator(frame_length=223), tm_cadu_framer()
        frames = []
        generator.message_port_pub = lambda port, m: frames.append(m)
        generator.build_frames(self._pdu(bytes(500)))
        cadus = []
        framer.message_port_pub = lambda port, m: cadus.append(m)
        for frame in frames:
            framer.add_asm(frame)
        block = cltu_burst_builder(acquisition_length=2, tail_length=2, fill_byte=0x55)
        bursts = [out for cadu in cadus for _, out in self._run(block, cadu)]
        self.assertEqual(len(bursts), 1)
        self.assertEqual(self._bytes(bursts[0]),
                         b"U" * 2 + b"".join(self._bytes(c) for c in cadus) + b"U" * 2)
        self.assertEqual(len(self._bytes(bursts[0])), 2 + 3 * (4 + 223) + 2)


if __name__ == '__main__':
    gr_unittest.run(qa_cltu_burst_builder)
