#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""What the TM chain relies on in gr-satellites' CCSDS Reed-Solomon blocks
(satellites.encode_rs / decode_rs, CCSDS 131.0-B-5 section 4): RS(255,223),
dual basis, interleaving, shortening, 16 correctable byte errors per
codeword, metadata kept. A gr-satellites update that changes any of this
fails here."""

import random
import time
import unittest

from gnuradio import blocks, gr, gr_unittest
import pmt

try:
    import satellites
except ImportError:  # the TC side needs no gr-satellites
    satellites = None

DUAL_BASIS = True  # CCSDS 131.0-B-5 4.3.9: dual basis representation


def _run(block, pdus, timeout_s=2.0):
    """Feed the PDUs through block in a running flowgraph; its outputs.
    A test expecting a drop passes a short timeout instead of waiting."""
    tb = gr.top_block()
    sink = blocks.message_debug()
    tb.msg_connect((block, "out"), (sink, "store"))
    tb.start()
    for pdu in pdus:
        block.to_basic_block()._post(pmt.intern("in"), pdu)
    deadline = time.time() + timeout_s
    while sink.num_messages() < len(pdus) and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    tb.stop()
    tb.wait()
    return [sink.get_message(i) for i in range(sink.num_messages())]


def _pdu(data, meta=None):
    meta = pmt.make_dict() if meta is None else meta
    return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))


def _payload(msg):
    return bytes(pmt.u8vector_elements(pmt.cdr(msg)))


@unittest.skipIf(satellites is None, "gr-satellites is not installed (needed for TM Reed-Solomon)")
class qa_tm_reed_solomon(gr_unittest.TestCase):

    def _encode(self, data, interleave, meta=None, timeout_s=2.0):
        out = _run(satellites.encode_rs(DUAL_BASIS, interleave), [_pdu(data, meta)], timeout_s)
        return out[0] if out else None

    def _decode(self, codeblock, interleave, timeout_s=2.0):
        out = _run(satellites.decode_rs(DUAL_BASIS, interleave), [_pdu(codeblock)], timeout_s)
        return _payload(out[0]) if out else None

    # 223 * I bytes in, 255 * I out; the frame appears unchanged at the front
    # (the code is systematic), followed by 32 * I check symbols.
    def test_001_codeblock_size_and_systematic(self):
        for interleave in (1, 2, 5):
            frame = bytes(random.Random(interleave).randrange(256) for _ in range(223 * interleave))
            codeblock = _payload(self._encode(frame, interleave))
            self.assertEqual(len(codeblock), 255 * interleave, f"I={interleave}")
            self.assertEqual(codeblock[:len(frame)], frame, f"I={interleave}")

    def test_002_round_trip(self):
        for interleave in (1, 5):
            frame = bytes(random.Random(10 + interleave).randrange(256) for _ in range(223 * interleave))
            self.assertEqual(self._decode(_payload(self._encode(frame, interleave)), interleave), frame)

    # t = 16: up to 16 byte errors per codeword are corrected; with I = 2
    # the codewords are interleaved byte by byte, so 32 consecutive byte
    # errors are 16 per codeword.
    def test_003_corrects_16_byte_errors_per_codeword(self):
        rng = random.Random(3)
        frame = bytes(rng.randrange(256) for _ in range(223))
        codeblock = bytearray(_payload(self._encode(frame, 1)))
        for position in rng.sample(range(255), 16):
            codeblock[position] ^= rng.randrange(1, 256)
        self.assertEqual(self._decode(bytes(codeblock), 1), frame)

        frame2 = bytes(rng.randrange(256) for _ in range(446))
        codeblock2 = bytearray(_payload(self._encode(frame2, 2)))
        for position in range(100, 132):  # a 32-byte burst: 16 errors in each codeword
            codeblock2[position] ^= 0xFF
        self.assertEqual(self._decode(bytes(codeblock2), 2), frame2)

    def test_004_17_byte_errors_are_not_corrected(self):
        rng = random.Random(4)
        frame = bytes(rng.randrange(256) for _ in range(223))
        codeblock = bytearray(_payload(self._encode(frame, 1)))
        for position in rng.sample(range(255), 17):
            codeblock[position] ^= rng.randrange(1, 256)
        self.assertNotEqual(self._decode(bytes(codeblock), 1, timeout_s=0.3), frame)

    # A shorter frame gives a shortened codeblock (virtual fill, CCSDS
    # 131.0-B-5 4.3.7): 32 check symbols per codeword are still appended.
    def test_005_shortened_codeblock(self):
        frame = bytes(range(200))
        codeblock = _payload(self._encode(frame, 1))
        self.assertEqual(len(codeblock), 232)
        self.assertEqual(self._decode(codeblock, 1), frame)

    # Nothing is split: a frame longer than 223 * I bytes, or not a multiple
    # of I, is dropped - fitting frames to the codeblock is the frame layer's job.
    def test_006_oversized_or_misaligned_frames_are_dropped(self):
        self.assertIsNone(self._encode(bytes(300), 1, timeout_s=0.3))
        self.assertIsNone(self._encode(bytes(223), 2, timeout_s=0.3))

    # The burst marker set by the frame layer must reach the Burst Builder.
    def test_007_metadata_is_kept(self):
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("burst_more"), pmt.PMT_T)
        self.assertTrue(pmt.equal(pmt.car(self._encode(bytes(223), 1, meta)), meta))


if __name__ == '__main__':
    gr_unittest.run(qa_tm_reed_solomon)
