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

from gnuradio.sage import lfsrScrambler, cltuFramer, bchEncoder


class qa_lfsr_cltu_bch_chain(gr_unittest.TestCase):

    def setUp(self):
        self.scrambler = lfsrScrambler()
        self.framer = cltuFramer()
        self.encoder = bchEncoder()
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

        # New chain order: Scrambler -> BCH Encoder -> CLTU Framer
        def _scrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                # Scrambler output goes into the encoder
                self.encoder.encodeBCH(msg)

        def _encoder_pub(port, msg):
            if pmt.eqv(port, pmt.intern("codewords")):
                # Encoder emits codeword PDUs which are then framed
                self.framer.addSequences(msg)

        def _framer_pub(port, msg):
            # Framer is now the last stage; capture its outputs
            self.captured_output.append((port, msg))

        self.scrambler.message_port_pub = _scrambler_pub
        self.framer.message_port_pub = _framer_pub
        self.encoder.message_port_pub = _encoder_pub

        return original_scrambler_pub, original_framer_pub, original_encoder_pub

    def _restore_chain(self, pubs):
        self.scrambler.message_port_pub = pubs[0]
        self.framer.message_port_pub = pubs[1]
        self.encoder.message_port_pub = pubs[2]

    def test_001_chain_produces_output(self):
        pubs = self._connect_chain()
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(42))

            input_data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])
            input_pdu = pmt.cons(meta, pmt.init_u8vector(len(input_data), list(input_data)))

            self.scrambler.handle_msg(input_pdu)
        finally:
            self._restore_chain(pubs)
        # Framer is last: it should produce one framed PDU per codeword
        self.assertEqual(len(self.captured_output), 2)

        # Verify the first output is from the framer and carries the meta
        out_port, out_msg = self.captured_output[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("pdu_out")))

        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.eqv(out_meta, meta))

        # Each framed output should be 18 bytes: 2 (start) + 8 (codeword) + 8 (tail)
        for _, msg in self.captured_output:
            out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            self.assertEqual(len(out_bytes), 18)

    def test_002_chain_matches_stagewise_reference(self):
        pubs = self._connect_chain()
        try:
            meta = pmt.make_dict()
            input_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
            input_pdu = pmt.cons(meta, pmt.init_u8vector(len(input_data), list(input_data)))
            self.scrambler.handle_msg(input_pdu)
        finally:
            self._restore_chain(pubs)
        # Framer is the last stage: collect framed outputs in order
        self.assertEqual(len(self.captured_output), 2)
        chain_output = bytearray()
        for _, msg in self.captured_output:
            chain_output.extend(pmt.u8vector_elements(pmt.cdr(msg)))
        chain_output = bytes(chain_output)
        # Stagewise reference for Scrambler -> Encoder -> Framer
        scrambled = bytes(self.scrambler.apply_scrambling(input_data))

        # Build reference codewords from scrambled payload
        bits = self.encoder._bytes_to_bits(scrambled)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        reference_codewords = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bits = stuffed_bits[i:i + 56]
            info_bytes = self.encoder._bits_to_bytes(info_bits)
            parity_bits = self.encoder._compute_parity_bits(info_bytes)

            parity_byte = 0
            for b in parity_bits:
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1

            reference_codewords.extend(info_bytes)
            reference_codewords.append(parity_byte)

        # Frame each codeword and concatenate
        reference_frames = bytearray()
        for i in range(0, len(reference_codewords), 8):
            codeword = reference_codewords[i:i+8]
            frame = struct.pack("!H", self.framer.startSequence) + codeword + struct.pack("!Q", self.framer.tailSequence)
            reference_frames.extend(frame)

        self.assertEqual(chain_output, bytes(reference_frames))

    def _run_chain(self, input_data):
        pubs = self._connect_chain()
        try:
            meta = pmt.make_dict()
            input_pdu = pmt.cons(meta, pmt.init_u8vector(len(input_data), list(input_data)))
            self.scrambler.handle_msg(input_pdu)
        finally:
            self._restore_chain(pubs)

        return meta, list(self.captured_output)

    def test_003_one_codeword(self):
        # 7-byte input -> 56 bits -> 1 BCH codeword -> 1 framed output
        input_data = bytes([i & 0xFF for i in range(7)])
        meta, outputs = self._run_chain(input_data)

        self.assertEqual(len(outputs), 1)
        for _, msg in outputs:
            out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            self.assertEqual(len(out_bytes), 18)

        # Build reference and compare
        scrambled = bytes(self.scrambler.apply_scrambling(input_data))
        bits = self.encoder._bytes_to_bits(scrambled)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        reference_codewords = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bits = stuffed_bits[i:i + 56]
            info_bytes = self.encoder._bits_to_bytes(info_bits)
            parity_bits = self.encoder._compute_parity_bits(info_bytes)

            parity_byte = 0
            for b in parity_bits:
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1

            reference_codewords.extend(info_bytes)
            reference_codewords.append(parity_byte)

        reference_frames = bytearray()
        for i in range(0, len(reference_codewords), 8):
            codeword = reference_codewords[i:i+8]
            frame = struct.pack("!H", self.framer.startSequence) + codeword + struct.pack("!Q", self.framer.tailSequence)
            reference_frames.extend(frame)

        chain_output = bytearray()
        for _, msg in outputs:
            chain_output.extend(pmt.u8vector_elements(pmt.cdr(msg)))

        self.assertEqual(bytes(chain_output), bytes(reference_frames))

    def test_004_two_codewords(self):
        # 8-byte input -> 64 bits -> padded to 112 -> 2 BCH codewords -> 2 framed outputs
        input_data = bytes([0x01,0x23,0x45,0x67,0x89,0xAB,0xCD,0xEF])
        meta, outputs = self._run_chain(input_data)

        self.assertEqual(len(outputs), 2)
        for _, msg in outputs:
            out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            self.assertEqual(len(out_bytes), 18)

        # Build reference same as in test_002 and compare
        scrambled = bytes(self.scrambler.apply_scrambling(input_data))
        bits = self.encoder._bytes_to_bits(scrambled)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        reference_codewords = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bits = stuffed_bits[i:i + 56]
            info_bytes = self.encoder._bits_to_bytes(info_bits)
            parity_bits = self.encoder._compute_parity_bits(info_bytes)

            parity_byte = 0
            for b in parity_bits:
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1

            reference_codewords.extend(info_bytes)
            reference_codewords.append(parity_byte)

        reference_frames = bytearray()
        for i in range(0, len(reference_codewords), 8):
            codeword = reference_codewords[i:i+8]
            frame = struct.pack("!H", self.framer.startSequence) + codeword + struct.pack("!Q", self.framer.tailSequence)
            reference_frames.extend(frame)

        chain_output = bytearray()
        for _, msg in outputs:
            chain_output.extend(pmt.u8vector_elements(pmt.cdr(msg)))

        self.assertEqual(bytes(chain_output), bytes(reference_frames))

    def test_005_three_codewords(self):
        # 15-byte input -> 120 bits -> padded to 168 -> 3 BCH codewords -> 3 framed outputs
        input_data = bytes([(i * 17) & 0xFF for i in range(15)])
        meta, outputs = self._run_chain(input_data)

        self.assertEqual(len(outputs), 3)
        for _, msg in outputs:
            out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            self.assertEqual(len(out_bytes), 18)

        scrambled = bytes(self.scrambler.apply_scrambling(input_data))
        bits = self.encoder._bytes_to_bits(scrambled)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        reference_codewords = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bits = stuffed_bits[i:i + 56]
            info_bytes = self.encoder._bits_to_bytes(info_bits)
            parity_bits = self.encoder._compute_parity_bits(info_bytes)

            parity_byte = 0
            for b in parity_bits:
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1

            reference_codewords.extend(info_bytes)
            reference_codewords.append(parity_byte)

        reference_frames = bytearray()
        for i in range(0, len(reference_codewords), 8):
            codeword = reference_codewords[i:i+8]
            frame = struct.pack("!H", self.framer.startSequence) + codeword + struct.pack("!Q", self.framer.tailSequence)
            reference_frames.extend(frame)

        chain_output = bytearray()
        for _, msg in outputs:
            chain_output.extend(pmt.u8vector_elements(pmt.cdr(msg)))

        self.assertEqual(bytes(chain_output), bytes(reference_frames))

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


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_cltu_bch_chain)
