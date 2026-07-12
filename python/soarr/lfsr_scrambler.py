#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
from gnuradio import gr
import pmt

REGISTER_LENGTH_REQUIRED = 8


class lfsr_scrambler(gr.basic_block):
    """
    Apply the CCSDS 231.0-B-3 TC pseudo-randomizer to a PDU's payload.

    Flow:
    - Receive a PDU on `in`
    - XOR the payload against a fixed LFSR-generated bit sequence,
      restarting from `seed` on every message (per-frame reset)
    - Publish the scrambled PDU on `out`, metadata unchanged
    """

    def __init__(self, seed=0xFF, register_length=8):
        """
        Args:
            seed (int): initial 8-bit LFSR register state. Only the low
                `register_length` bits are used.
            register_length (int): LFSR register width in bits. Only `8`
                is supported (the CCSDS 231.0-B-3 randomizer is defined
                for an 8-bit register).

        Raises:
            ValueError: register_length is not 8.
        """
        gr.basic_block.__init__(self,
            name="LFSR Scrambler",
            in_sig=None,
            out_sig=None)

        if register_length != REGISTER_LENGTH_REQUIRED:
            raise ValueError(
                f"CCSDS randomizer requires register_length={REGISTER_LENGTH_REQUIRED}, got {register_length}."
            )

        # Store parameters
        self.seed = seed
        self.register_length = register_length

        # Define message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Register handler for incoming messages
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

    def handle_msg(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.
                No metadata keys are read or required.

        Publishes:
            "out" (pmt_pair): PDU with the payload XORed against the
                randomizer sequence; metadata unchanged, length preserved.

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - payload is not a u8vector (error - same)
            - scrambling or publishing fails (error - same)
        """
        if not pmt.is_pair(msg):
            self.logger.error("Input message is not a pair.")
            return

        meta = pmt.car(msg)
        body = pmt.cdr(msg)

        if not pmt.is_u8vector(body):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        # Full body from here on wrapped in catch-log-drop, including the
        # final publish - a raise anywhere in here must never escape this
        # handler.
        try:
            pdu_data = pmt.u8vector_elements(body)
            scrambled_data = self.apply_scrambling(pdu_data)

            new_pdu = pmt.cons(meta, pmt.init_u8vector(len(scrambled_data), scrambled_data))
            self.message_port_pub(pmt.intern("out"), new_pdu)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to scramble or publish message: {exc}")
            return

    def apply_scrambling(self, data):
        """
        Args:
            data: sequence of payload bytes to scramble.

        Returns:
            bytearray: `data` XORed (MSB first) against the CCSDS
                231.0-B-3 randomizer sequence (h(x) = x^8 + x^6 + x^4 +
                x^3 + x^2 + x + 1), regenerated from `seed` on every call.
        """
        total_bits = len(data) * 8
        scramble_seq = [
            (self.seed >> (self.register_length - 1 - i)) & 1
            for i in range(self.register_length)
        ]

        while len(scramble_seq) < total_bits:
            n = len(scramble_seq)
            next_bit = (
                scramble_seq[n - 2]
                ^ scramble_seq[n - 4]
                ^ scramble_seq[n - 5]
                ^ scramble_seq[n - 6]
                ^ scramble_seq[n - 7]
                ^ scramble_seq[n - 8]
            )
            scramble_seq.append(next_bit)

        output = bytearray()
        seq_idx = 0

        for byte in data:
            out_byte = 0
            for i in range(8):
                scramble_bit = scramble_seq[seq_idx]
                seq_idx += 1
                
                # Data bit (MSB first)
                data_bit = (byte >> (7 - i)) & 1
                
                # XOR the data and scrambler bits
                res_bit = data_bit ^ scramble_bit
                out_byte = (out_byte << 1) | res_bit
            
            output.append(out_byte)
        return output
