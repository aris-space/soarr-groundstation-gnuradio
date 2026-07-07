#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import lfsrScrambler, bchEncoder, cltuFramer, cltuDeframer, bchDecoder, lfsrDescrambler


class qa_lfsr_receive_chain(gr_unittest.TestCase):

    def setUp(self):
        self.scrambler = lfsrScrambler()
        self.encoder = bchEncoder()
        self.framer = cltuFramer()

        self.deframer = cltuDeframer()
        self.decoder = bchDecoder()
        self.descrambler = lfsrDescrambler()

        self.frames = []
        self.recovered = []

    def tearDown(self):
        self.scrambler = None
        self.encoder = None
        self.framer = None
        self.deframer = None
        self.decoder = None
        self.descrambler = None
        self.frames = []
        self.recovered = []

    def _produce_frames(self, meta, payload_bytes):
        # Hook scrambler -> encoder -> framer to collect frame bytes
        original_scrambler_pub = self.scrambler.message_port_pub
        original_encoder_pub = self.encoder.message_port_pub
        original_framer_pub = self.framer.message_port_pub

        def _scrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                self.encoder.encodeBCH(msg)

        def _encoder_pub(port, msg):
            if pmt.eqv(port, pmt.intern("codewords")):
                self.framer.addSequences(msg)

        def _framer_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                # capture framed bytes
                frame_bytes = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
                self.frames.append((pmt.car(msg), frame_bytes))

        self.scrambler.message_port_pub = _scrambler_pub
        self.encoder.message_port_pub = _encoder_pub
        self.framer.message_port_pub = _framer_pub

        try:
            pdu = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
            self.scrambler.handle_msg(pdu)
        finally:
            self.scrambler.message_port_pub = original_scrambler_pub
            self.encoder.message_port_pub = original_encoder_pub
            self.framer.message_port_pub = original_framer_pub

    def _run_receive_chain(self):
        # Hook deframer -> decoder -> descrambler -> capture
        original_deframer_pub = self.deframer.message_port_pub
        original_decoder_pub = self.decoder.message_port_pub
        original_descrambler_pub = self.descrambler.message_port_pub

        # deframer publishes PDU (dict, framed payload)
        def _deframer_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                # feed into decoder
                self.decoder.error_correction_mode(msg)

        # decoder publishes corrected PDUs -> feed into descrambler
        def _decoder_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                self.descrambler.descramble_msg(msg)

        # capture descrambler outputs
        def _descrambler_pub(port, msg):
            if pmt.eqv(port, pmt.intern("out")):
                self.recovered.append((pmt.car(msg), bytes(pmt.u8vector_elements(pmt.cdr(msg)))))

        self.deframer.message_port_pub = _deframer_pub
        self.decoder.message_port_pub = _decoder_pub
        self.descrambler.message_port_pub = _descrambler_pub

        try:
            # Feed each captured frame byte-stream into the deframer as if it arrived over the air
            for meta, frame_bytes in self.frames:
                self.deframer.process_bytes(frame_bytes)
        finally:
            self.deframer.message_port_pub = original_deframer_pub
            self.decoder.message_port_pub = original_decoder_pub
            self.descrambler.message_port_pub = original_descrambler_pub

    def test_001_end_to_end_receive(self):
        # original payload shorter than a full 56-bit block to force fill bits
        PAYLOAD = bytes([0x11, 0x22, 0x33])
        meta = pmt.make_dict()

        # produce frames using the transmit chain
        self._produce_frames(meta, PAYLOAD)
        self.assertTrue(len(self.frames) > 0)

        # run the receive-side chain
        self._run_receive_chain()

        # we expect at least one recovered PDU
        self.assertTrue(len(self.recovered) >= 1)

        # Final recovered payload should include original payload at start
        # Combine recovered payloads if multiple codewords
        recovered_bytes = bytearray()
        for m, payload in self.recovered:
            recovered_bytes.extend(payload)

        # The encoder may have padded/fill bits; after decode+descramble the start should equal PAYLOAD
        self.assertEqual(bytes(recovered_bytes[:len(PAYLOAD)]), PAYLOAD)

    def test_002_bch_parity_encoder_computation(self):
        """Test that encoder computes parity correctly on known data."""
        # Use actual flowgraph data: scrambler output before encoder
        info_bytes = bytes([0x0f, 0x78, 0x32, 0x4b, 0xf7, 0x00, 0xcf])
        
        parity_bits = self.encoder._compute_parity_bits(info_bytes)
        self.assertEqual(len(parity_bits), 7, "Parity should be 7 bits")
        
        # Pack parity + filler bit
        parity_byte = 0
        for bit in parity_bits:
            parity_byte = (parity_byte << 1) | bit
        parity_byte = (parity_byte << 1)  # filler bit = 0
        parity_byte = parity_byte & 0xFF
        
        # Expected from flowgraph output: 0x04
        self.assertEqual(parity_byte, 0x04, f"Parity byte should be 0x04, got {hex(parity_byte)}")

    def test_003_bch_decoder_validation_no_errors(self):
        """Test that decoder correctly validates a perfect codeword with no false corrections."""
        # Use actual flowgraph data
        info_bytes = bytes([0x0f, 0x78, 0x32, 0x4b, 0xf7, 0x00, 0xcf])
        
        # Compute parity using encoder
        parity_bits = self.encoder._compute_parity_bits(info_bytes)
        parity_byte = 0
        for bit in parity_bits:
            parity_byte = (parity_byte << 1) | bit
        parity_byte = (parity_byte << 1)
        parity_byte = parity_byte & 0xFF
        
        # Build perfect 8-byte codeword
        codeword = info_bytes + bytes([parity_byte])
        
        # Feed to decoder
        meta = pmt.make_dict()
        pdu = pmt.cons(meta, pmt.init_u8vector(len(codeword), list(codeword)))
        
        outputs = []
        orig_pub = self.decoder.message_port_pub
        try:
            self.decoder.message_port_pub = lambda port, msg: outputs.append((port, msg))
            self.decoder.error_correction_mode(pdu)
        finally:
            self.decoder.message_port_pub = orig_pub
        
        # Decoder must produce output
        self.assertEqual(len(outputs), 1, "Decoder should produce one output PDU")
        
        # Extract decoded bytes
        _, out_msg = outputs[0]
        out_meta = pmt.car(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        
        # Decoded data should match original (no false corrections)
        self.assertEqual(out_bytes, info_bytes, f"Decoded bytes {out_bytes.hex()} should match original {info_bytes.hex()}")
        
        # Check that NO error flag was set (perfect codeword)
        has_error = pmt.dict_has_key(out_meta, pmt.intern("bch_error"))
        if has_error:
            error_val = pmt.dict_ref(out_meta, pmt.intern("bch_error"))
            self.assertFalse(pmt.to_bool(error_val), "Perfect codeword should not set bch_error flag")

    def test_004_bch_parity_consistency(self):
        """Test that encoder and decoder compute same parity from same info bytes."""
        test_cases = [
            bytes([0x00] * 7),
            bytes([0xFF] * 7),
            bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77]),
            bytes([0x0f, 0x78, 0x32, 0x4b, 0xf7, 0x00, 0xcf]),
        ]
        
        for info_bytes in test_cases:
            with self.subTest(info=info_bytes.hex()):
                parity_enc = self.encoder._compute_parity_bits(info_bytes)
                parity_dec = self.decoder._compute_parity_bits(info_bytes)
                
                self.assertEqual(parity_enc, parity_dec, 
                    f"Encoder and decoder parity mismatch for {info_bytes.hex()}: "
                    f"encoder={list(parity_enc)}, decoder={list(parity_dec)}")


if __name__ == '__main__':
    gr_unittest.run(qa_lfsr_receive_chain)
