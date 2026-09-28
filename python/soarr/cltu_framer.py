#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import struct
import pmt
from gnuradio import gr


CODEWORD_SIZE_BYTES = 8  # one BCH (63,56) codeword incl. its filler bit

START_SEQUENCE_MIN = 0x0000  # 16 bits, struct '!H'
START_SEQUENCE_MAX = 0xFFFF
TAIL_SEQUENCE_MIN = 0x0000000000000000  # 64 bits, struct '!Q'
TAIL_SEQUENCE_MAX = 0xFFFFFFFFFFFFFFFF


class cltu_framer(gr.basic_block):
    """
    Frame a PDU of BCH codewords into one CLTU (CCSDS 231.0-B-4
    Figure 5-1): prepend a start sequence, append a tail sequence.

    Flow:
    - Receive a PDU on `in`: all codewords of one transfer frame, as
      bch_encoder publishes them (a non-zero multiple of 8 bytes)
    - Prepend `start_sequence`, append `tail_sequence` (both re-packed
      fresh on every message, so runtime reassignment takes effect
      immediately)
    - Publish the framed PDU on `out`, metadata unchanged
    """

    def __init__(self, start_sequence: int = 0xEB90, tail_sequence: int = 0xC5C5C5C5C5C5C579):
        """
        Args:
            start_sequence (int): CLTU start sequence, packed as
                big-endian 2 bytes.
            tail_sequence (int): CLTU tail sequence, packed as
                big-endian 8 bytes.

        Raises:
            ValueError: start_sequence or tail_sequence is outside its
                packed width (16 bits / 64 bits respectively). Both
                attributes remain directly mutable after construction
                (see class docstring) - this check only catches values
                supplied at construction time, not a later reassignment.
        """
        gr.basic_block.__init__(self,
            name="CLTU Framer",
            in_sig=None,
            out_sig=None)

        if not (START_SEQUENCE_MIN <= start_sequence <= START_SEQUENCE_MAX):
            raise ValueError(
                f"start_sequence must be a 16-bit value ({START_SEQUENCE_MIN}-{START_SEQUENCE_MAX}), got {start_sequence}."
            )
        if not (TAIL_SEQUENCE_MIN <= tail_sequence <= TAIL_SEQUENCE_MAX):
            raise ValueError(
                f"tail_sequence must be a 64-bit value ({TAIL_SEQUENCE_MIN}-{TAIL_SEQUENCE_MAX}), got {tail_sequence}."
            )

        # Enables change during runtime
        self.start_sequence = start_sequence
        self.tail_sequence = tail_sequence

        # Message Ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.add_sequences)

    def add_sequences(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload,
                one or more whole 8-byte BCH codewords. No metadata keys
                are read or required.

        Publishes:
            "out" (pmt_pair): one CLTU: `start_sequence` (2 bytes) + the
                codewords + `tail_sequence` (8 bytes); metadata unchanged.

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - payload is not a u8vector (error - same)
            - payload is empty or not a multiple of 8 bytes (error - same)
            - packing the sequences or publishing fails (error - same; covers a bad start_sequence/tail_sequence value reassigned after construction, which bypasses __init__'s validation)
        """
        # Full body wrapped in catch-log-drop, including the final
        # publish - a raise anywhere in here (e.g. struct.pack on a
        # runtime-reassigned out-of-range sequence value) must never
        # escape this handler.
        try:
            if not pmt.is_pair(msg):
                self.logger.error("Input message is not a pair.")
                return

            # unpack PDU to get meta and body
            meta = pmt.car(msg)
            body_pmt = pmt.cdr(msg)

            if not pmt.is_u8vector(body_pmt):
                self.logger.error("Input message body is not a PDU.")
                return

            # Convert body to bytes for easy concatenation
            payload_bytes = bytes(pmt.u8vector_elements(body_pmt))

            # Check payload size: a whole number of codewords
            if len(payload_bytes) == 0 or len(payload_bytes) % CODEWORD_SIZE_BYTES != 0:
                self.logger.error(
                    f"Payload size is {len(payload_bytes)} bytes, expected a non-zero multiple of "
                    f"{CODEWORD_SIZE_BYTES} bytes (whole BCH codewords)"
                )
                return

            # CCSDS 231.0-B-4 start/tail sequences.
            start_seq = struct.pack('!H', self.start_sequence)
            # The tail sequence is the "end-of-transmission" pattern.
            tail_seq = struct.pack('!Q', self.tail_sequence)

            # CLTU build: [Start] [BCH-Blocks] [Tail]
            full_cltu = start_seq + payload_bytes + tail_seq

            # pack to PDU and send it out
            out_msg = pmt.cons(meta, pmt.init_u8vector(len(full_cltu), list(full_cltu)))
            self.logger.trace(f"CLTU got framed with size {len(full_cltu)} bytes")
            self.message_port_pub(pmt.intern("out"), out_msg)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to frame or publish message: {exc}")
            return
