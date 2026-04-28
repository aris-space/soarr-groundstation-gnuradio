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

        def _scrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                self.framer.addSequences(msg)

        def _framer_pub(port, msg):
            if pmt.eqv(port, pmt.intern("pdu_out")):
                self.encoder.encodeBCH(msg)

        def _encoder_pub(port, msg):
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

        self.assertEqual(len(self.captured_output), 1)

        out_port, out_msg = self.captured_output[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("codewords")))

        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.eqv(out_meta, meta))

        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        # 8-byte input -> scrambler keeps 8 bytes -> framer outputs 18 bytes
        # BCH then pads to 3 codewords (24 bytes output).
        self.assertEqual(len(out_bytes), 24)

    def test_002_chain_matches_stagewise_reference(self):
        pubs = self._connect_chain()
        try:
            meta = pmt.make_dict()
            input_data = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
            input_pdu = pmt.cons(meta, pmt.init_u8vector(len(input_data), list(input_data)))
            self.scrambler.handle_msg(input_pdu)
        finally:
            self._restore_chain(pubs)

        self.assertEqual(len(self.captured_output), 1)
        _, out_msg = self.captured_output[0]
        chain_output = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        scrambled = bytes(self.scrambler.apply_scrambling(input_data))
        framed = struct.pack("!H", self.framer.startSequence) + scrambled + struct.pack("!Q", self.framer.tailSequence)

        bits = self.encoder._bytes_to_bits(framed)
        stuffed_bits, _ = self.encoder._apply_fill_bits(bits)

        reference = bytearray()
        for i in range(0, len(stuffed_bits), 56):
            info_bits = stuffed_bits[i:i + 56]
            info_bytes = self.encoder._bits_to_bytes(info_bits)
            parity_bits = self.encoder._compute_parity_bits(info_bytes)

            parity_byte = 0
            for b in parity_bits:
                parity_byte = (parity_byte << 1) | b
            parity_byte <<= 1

            reference.extend(info_bytes)
            reference.append(parity_byte)

        self.assertEqual(chain_output, bytes(reference))


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_cltu_bch_chain)
