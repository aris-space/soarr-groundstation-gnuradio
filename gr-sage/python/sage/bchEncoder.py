#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import pmt
from gnuradio import gr

INPUT_SIZE = 56  # 7 bytes * 8 bits/byte
OUTPUT_SIZE = 64  # 8 bytes * 8 bits/byte (56 bits + 7 parity + 1 filler)
PARITY_BITS = 7  # BCH code generates 7 parity bits
FILLER_BITS = 1  # Appended filler bit

class bchEncoder(gr.basic_block):
    """
    BCH (63,56) Encoder block implementing CCSDS 231.0-B-4 standard.
    Generates 7 parity check bits from 56 information bits using the
    generator polynomial g(x) = x^7 + x^6 + x^2 + 1.
    """
    def __init__(self, polynomial=0xC5):
        gr.basic_block.__init__(self,
            name="bchEncoder",
            in_sig=None,
            out_sig=None
        )

        self.logger.info(f"Initializing BCH Encoder with polynomial: 0x{polynomial:02x}")

        # Message Ports
        self.message_port_register_in(pmt.intern("message"))
        self.message_port_register_out(pmt.intern("codewords"))
        self.set_msg_handler(pmt.intern("message"), self.encodeBCH)

        # Generator polynomial g(x) = x^7 + x^6 + x^2 + 1 in binary: 11000101 (0xC5)
        # Represents feedback taps for the LFSR
        self.polynomial = polynomial

    def _compute_parity_bits(self, data_bits):
        """
        Compute complemented parity bits for the CCSDS (63,56) BCH code.

        Method:
        - Build m(x) from the 56 information bits (MSB-first)
        - Compute remainder r(x) of x^7 * m(x) divided by g(x)
        - Complement the 7 parity bits per CCSDS 231.0-B-4 §3.3.1

        Returns:
        - 7 bits as bytes-like values [P6, ..., P0], already complemented
        """
        message = int.from_bytes(data_bits, "big")
        dividend = message << PARITY_BITS

        # Polynomial long division in GF(2) for 63-bit codeword space.
        for bit_pos in range((INPUT_SIZE + PARITY_BITS) - 1, PARITY_BITS - 1, -1):
            if (dividend >> bit_pos) & 1:
                dividend ^= self.polynomial << (bit_pos - PARITY_BITS)

        remainder = dividend & ((1 << PARITY_BITS) - 1)

        # Complement parity bits and return as [P6..P0].
        complemented = remainder ^ ((1 << PARITY_BITS) - 1)
        return bytes([(complemented >> i) & 1 for i in range(PARITY_BITS - 1, -1, -1)])

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

    def _apply_fill_bits(self, bits):
        remainder = len(bits) % INPUT_SIZE
        if remainder == 0:
            return bits, 0

        fill_count = INPUT_SIZE - remainder
        fill_bits = [(i % 2) for i in range(fill_count)]  # 0,1,0,1,...
        return bits + fill_bits, fill_count

    def encodeBCH(self, msg):
        """
        Encode incoming PDU with BCH parity bits.
        Input: PDU with 56-bit information payload
        Output: PDU with 64-bit encoded data (56 info + 7 parity + 1 filler)
        """
        # Unpack PDU to get meta and body
        meta = pmt.car(msg)
        body_pmt = pmt.cdr(msg)

        if not pmt.is_u8vector(body_pmt):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        # Convert body to bytes for processing
        payload_bytes = bytes(pmt.u8vector_elements(body_pmt))

        if len(payload_bytes) == 0:
            self.logger.error("Input payload is empty.")
            return

        try:
            # Apply CCSDS fill pattern to complete integral 56-bit codewords.
            information_bits = self._bytes_to_bits(payload_bytes)
            stuffed_bits, fill_count = self._apply_fill_bits(information_bits)

            if fill_count > 0:
                self.logger.debug(f"Applied {fill_count} fill bits to complete BCH codeword boundary.")

            for bit_start in range(0, len(stuffed_bits), INPUT_SIZE):
                info_bits = stuffed_bits[bit_start:bit_start + INPUT_SIZE]
                info_bytes = self._bits_to_bytes(info_bits)

                parity_bits = self._compute_parity_bits(info_bytes)

                # Add parity bits (7 bits) + trailing filler bit (1 bit) per codeword.
                parity_byte = 0
                for bit in parity_bits:
                    parity_byte = (parity_byte << 1) | bit
                parity_byte = (parity_byte << FILLER_BITS)

                output_data = bytearray()
                output_data.extend(info_bytes)
                output_data.append(parity_byte)
                
                # Create output PDU with encoded data (PMT expects a sequence of ints)
                encoded_pdu = pmt.cons(meta, pmt.init_u8vector(len(output_data), list(output_data)))
                
                self.logger.debug(f"Encoded PDU: {len(output_data)} bytes")
                
                # Send the encoded PDU out
                self.message_port_pub(pmt.intern("codewords"), encoded_pdu)
            
            self.logger.info(f"OK")
            
        except Exception as e:
            self.logger.error(f"BCH encoding error: {str(e)}")
            return

