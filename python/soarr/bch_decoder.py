#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

INPUT_SIZE = 64  # 8 bytes * 8 bits/byte (56 bits + 7 parity + 1 filler)
OUTPUT_SIZE = 56  # 7 bytes * 8 bits/byte (56 information bits)
PARITY_BITS = 7  # BCH code generates 7 parity bits
FILLER_BITS = 1  # Appended filler bit

INPUT_DATA_SIZE = INPUT_SIZE - FILLER_BITS

class bch_decoder(gr.basic_block):
    """
    Corrects up to 2 bit errors in a CCSDS 231.0-B-4 (63,56) BCH codeword,
    recovering the 56-bit information field, or drops the message if the
    codeword can't be corrected within that bound.
    """
    def __init__(self, mode:int = 0, generator_polynomial:int=0xC5):
        """
        Args:
            mode (int): Decode mode. Only 0 (brute-force search over all
                0-, 1-, and 2-bit corrections) is implemented.
            generator_polynomial (int): BCH generator polynomial g(x), as
                an 8-bit value. Default 0xC5 matches bch_encoder's own
                default, CCSDS 231.0-B-4's g(x) = x^7 + x^6 + x^2 + 1.

        Raises:
            ValueError: mode is not 0, or generator_polynomial is
                outside 0x00-0xFF.
        """
        gr.basic_block.__init__(self,
            name="bch_decoder",
            in_sig=None,
            out_sig=None)

        self.mode = mode
        self.generator_polynomial = generator_polynomial

        # Validate parameters
        # Mode 0 is the only supported mode for now, but we can add more modes in the future if needed.
        if self.mode != 0:
            raise ValueError(f"Unsupported mode: {self.mode}. Currently, only mode 0 is supported.")

        # Generator polynomial g(x) (encoder) in binary: e.g. 0xC5
        if generator_polynomial < 0x00 or generator_polynomial > 0xFF:
            raise ValueError(f"Invalid generator polynomial: 0x{generator_polynomial:02x}. Must be between 0x00 and 0xFF.")

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        self.set_msg_handler(pmt.intern("in"), self.error_correction_mode)


    def _compute_parity_bits(self, data_bits):
        """Compute complemented BCH parity bits for a 56-bit information field."""
        expected_length = OUTPUT_SIZE // 8
        if len(data_bits) != expected_length:
            raise ValueError(f"Input data must be {expected_length} bytes long, got {len(data_bits)} bytes.")

        message = int.from_bytes(data_bits, "big")
        dividend = message << PARITY_BITS

        for bit_pos in range((OUTPUT_SIZE + PARITY_BITS) - 1, PARITY_BITS - 1, -1):
            if (dividend >> bit_pos) & 1:
                dividend ^= self.generator_polynomial << (bit_pos - PARITY_BITS)

        remainder = dividend & ((1 << PARITY_BITS) - 1)
        complemented = remainder ^ ((1 << PARITY_BITS) - 1)
        return bytes([(complemented >> i) & 1 for i in range(PARITY_BITS - 1, -1, -1)])

    def _codeword_is_valid(self, bits):
        """Validate a 63-bit BCH codeword against the encoder parity convention."""
        info_bits = bits[:OUTPUT_SIZE]
        parity_bits = bits[OUTPUT_SIZE:OUTPUT_SIZE + PARITY_BITS]
        expected_parity = self._compute_parity_bits(self._bits_to_bytes(info_bits))
        return list(expected_parity) == parity_bits

    def _decode(self, data) -> list[int] | None:
        """Decode a BCH(63,56) codeword by searching for a valid correction."""
        if len(data) != INPUT_SIZE:
            raise ValueError(f"Input data must be {INPUT_SIZE} bits long, got {len(data)} bits.")
        
        # Cut out the filler bit (last bit) and work with the 63 bits of data+parity
        data = data[:INPUT_SIZE - FILLER_BITS]

        if self._codeword_is_valid(data):
            self.logger.debug("No error detected: codeword parity matches")
            return data[:OUTPUT_SIZE]

        for first_pos in range(INPUT_DATA_SIZE):
            corrected = list(data)
            corrected[first_pos] ^= 1
            if self._codeword_is_valid(corrected):
                self.logger.info(f"1-bit error corrected at position: {first_pos}")
                return corrected[:OUTPUT_SIZE]

        for first_pos in range(INPUT_DATA_SIZE - 1):
            for second_pos in range(first_pos + 1, INPUT_DATA_SIZE):
                corrected = list(data)
                corrected[first_pos] ^= 1
                corrected[second_pos] ^= 1
                if self._codeword_is_valid(corrected):
                    self.logger.info(f"2-bit error(s) corrected at positions: [{first_pos}, {second_pos}]")
                    return corrected[:OUTPUT_SIZE]

        return None


    def _bytes_to_bits(self, data_bytes):
        bits = []
        for byte in data_bytes:
            for bit_idx in range(7, -1, -1):
                bits.append((byte >> bit_idx) & 1)
        return bits
    
    def _bits_to_bytes(self, bits):
        output = bytearray()
        for start in range(0, len(bits), 8):
            value = 0
            for bit in bits[start:start + 8]:
                value = (value << 1) | bit
            output.append(value)
        return bytes(output)
    

    def error_correction_mode(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.
                Payload must be exactly 8 bytes (64 bits: 56 info + 7
                parity + 1 filler), the BCH codeword to correct.

        Publishes:
            "out" (pmt_pair): PDU with the same metadata dict and a
                7-byte u8vector payload, the corrected 56-bit information
                field. Published only when correction succeeds.

        Drops when:
            - msg is not a pair (warn - raw RF data, malformed before any structural check)
            - payload is not a u8vector (warn - same)
            - metadata is not a dict (warn - same)
            - payload is not exactly 8 bytes, or any other decoding error (warn - same)
            - no 0-, 1-, or 2-bit correction produces a valid codeword (warn - uncorrectable, same)
        """
        try:
            # Expecting a PDU with dict and payload
            if not pmt.is_pair(msg):
                self.logger.warn(f"Received non-PDU message: {msg}")
                return None

            # Extract the dict and payload from the PDU
            dict_msg = pmt.car(msg)
            payload_u8vector = pmt.cdr(msg)

            if not pmt.is_u8vector(payload_u8vector):
                self.logger.warn(f"Received message with non-u8vector payload: {msg}")
                return None

            if not pmt.is_dict(dict_msg):
                self.logger.warn(f"Received message with non-dict metadata: {msg}")
                return None

            # they are already in Bytes, so we can directly convert them to bits for processing
            payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))

            # Log the received data before error correction
            self.logger.debug(f"Received message for BCH error correction: {payload_bytes}")

            corrected_bits = self._decode(self._bytes_to_bits(payload_bytes))
            if corrected_bits is None:
                self.logger.warn("Message is dropped by BCH decoder")
                return None

            corrected_bytes = bytes(self._bits_to_bytes(corrected_bits))

            # Log the corrected message after error correction
            self.logger.debug(f"Corrected message: {corrected_bytes}")

            # Create a new PDU with the same dict and corrected payload
            corrected_payload = pmt.init_u8vector(len(corrected_bytes), list(corrected_bytes))
            msg = pmt.cons(dict_msg, corrected_payload)

            # Send the corrected message to the output port
            self.message_port_pub(pmt.intern("out"), msg)

            self.logger.debug("OK")
            return msg
        except Exception as exc:
            self.logger.warn(f"Failed to decode or publish message: {exc}")
            return None

