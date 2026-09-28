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

from gnuradio.soarr import lfsr_scrambler, cltu_framer, bch_encoder


class qa_lfsr_cltu_bch_chain(gr_unittest.TestCase):

    def setUp(self):
        self.scrambler = lfsr_scrambler()
        self.framer = cltu_framer()
        self.encoder = bch_encoder()
        self.captured_output = []

    def tearDown(self):
        self.scrambler = None
        self.framer = None
        self.encoder = None
        self.captured_output = []

    def _connect_chain(self):
        original_scrambler_pub = self.scrambler.message_port_pub
        original_framer_pub = self.framer.message_port_pub
        original_encoder_pub = self.encoder.message_port_pub

        # Chain order: Scrambler -> BCH Encoder -> CLTU Framer
        def _scrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                self.encoder.encode_bch(msg)

        def _encoder_pub(port, msg):
            if pmt.eqv(port, pmt.intern("codewords")):
                self.framer.add_sequences(msg)

        def _framer_pub(port, msg):
            # Framer is the last stage; capture its outputs
            self.captured_output.append((port, msg))

        self.scrambler.message_port_pub = _scrambler_pub
        self.framer.message_port_pub = _framer_pub
        self.encoder.message_port_pub = _encoder_pub

        return original_scrambler_pub, original_framer_pub, original_encoder_pub

    def _restore_chain(self, pubs):
        self.scrambler.message_port_pub = pubs[0]
        self.framer.message_port_pub = pubs[1]
        self.encoder.message_port_pub = pubs[2]

    def _run_chain(self, input_data, meta=None):
        meta = meta if meta is not None else pmt.make_dict()
        pubs = self._connect_chain()
        try:
            input_pdu = pmt.cons(meta, pmt.init_u8vector(len(input_data), list(input_data)))
            self.scrambler.handle_msg(input_pdu)
        finally:
            self._restore_chain(pubs)

        return meta, list(self.captured_output)

    def _reference_cltu(self, input_data):
        """Stagewise reference: scramble, BCH-encode every 56-bit block,
        then wrap all codewords in one start/tail pair (CCSDS 231.0-B-4
        Figure 5-1: one CLTU = start sequence + N codewords + tail sequence)."""
        scrambled = bytes(self.scrambler.apply_scrambling(input_data))
        bits = self.encoder._bytes_to_bits(scrambled)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        codewords = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bytes = self.encoder._bits_to_bytes(stuffed_bits[i:i + 56])
            parity_byte = 0
            for b in self.encoder._compute_parity_bits(info_bytes):
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1  # trailing filler bit
            codewords.extend(info_bytes)
            codewords.append(parity_byte)

        return (struct.pack("!H", self.framer.start_sequence) + bytes(codewords)
                + struct.pack("!Q", self.framer.tail_sequence))

    def _assert_one_cltu(self, input_data, expected_codewords):
        _, outputs = self._run_chain(input_data)

        # One input frame -> exactly one CLTU, however many codewords it has
        self.assertEqual(len(outputs), 1)
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(outputs[0][1])))
        self.assertEqual(len(out_bytes), 2 + 8 * expected_codewords + 8)
        self.assertEqual(out_bytes, self._reference_cltu(input_data))

    def test_001_chain_produces_one_cltu_with_metadata(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(42))

        input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])
        _, outputs = self._run_chain(input_data, meta)

        self.assertEqual(len(outputs), 1)
        out_port, out_msg = outputs[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        # The input metadata survives the chain (bch_encoder adds "filled")
        out_meta = pmt.car(out_msg)
        self.assertEqual(pmt.to_long(pmt.dict_ref(out_meta, pmt.intern("frame_id"), pmt.PMT_NIL)), 42)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("filled")))

    def test_002_chain_matches_stagewise_reference(self):
        # 8 bytes -> 64 bits -> padded to 112 -> 2 codewords in one CLTU
        self._assert_one_cltu(bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF]), 2)

    def test_003_one_codeword(self):
        # 7 bytes -> 56 bits -> 1 codeword -> 18-byte CLTU
        self._assert_one_cltu(bytes([i & 0xFF for i in range(7)]), 1)

    def test_004_two_codewords(self):
        # 14 bytes -> 112 bits -> 2 codewords, no fill bits
        self._assert_one_cltu(bytes(range(14)), 2)

    def test_005_three_codewords(self):
        # 15 bytes -> 120 bits -> padded to 168 -> 3 codewords
        self._assert_one_cltu(bytes([(i * 17) & 0xFF for i in range(15)]), 3)

    def test_006_fill_bits_padding(self):
        # Use 18 bytes -> 144 bits; 144 % 56 = 32 -> fill_count should be 24
        data = bytes([0x00] * 18)
        bits = self.encoder._bytes_to_bits(data)
        self.assertEqual(len(bits), 18 * 8)

        stuffed_bits, fill_count = self.encoder._apply_fill_bits(bits)
        self.assertEqual(fill_count, 24)
        self.assertEqual(len(stuffed_bits), 168)

        # Verify the fill pattern: 0,1,0,1,... for fill_count
        expected_fill = [(i % 2) for i in range(fill_count)]
        self.assertEqual(stuffed_bits[-fill_count:], expected_fill)

    def test_007_consecutive_frames_give_one_cltu_each(self):
        # Two frames of different lengths -> two independent CLTUs
        _, first = self._run_chain(bytes(range(20)))  # 3 codewords
        self.captured_output = []
        _, second = self._run_chain(bytes(range(5)))  # 1 codeword

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(pmt.u8vector_elements(pmt.cdr(first[0][1]))), 2 + 24 + 8)
        self.assertEqual(len(pmt.u8vector_elements(pmt.cdr(second[0][1]))), 2 + 8 + 8)


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_cltu_bch_chain)
