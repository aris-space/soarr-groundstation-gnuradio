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


PAYLOAD_SIZE_BYTES = 8  # one BCH codeword per CLTU frame, per bch_encoder's output

START_SEQUENCE_MIN = 0x0000  # 16 bits, struct '!H'
START_SEQUENCE_MAX = 0xFFFF
TAIL_SEQUENCE_MIN = 0x0000000000000000  # 64 bits, struct '!Q'
TAIL_SEQUENCE_MAX = 0xFFFFFFFFFFFFFFFF


class cltu_framer(gr.basic_block):
    """
    Frame an 8-byte BCH codeword PDU into a CLTU (CCSDS 231.0-B-4):
    prepend a start sequence, append a tail sequence.

    Flow:
    - Receive a PDU on `in`, payload must be exactly 8 bytes
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
                exactly 8 bytes (one BCH codeword). No metadata keys are
                required; "filled", if present, only affects logging.

        Publishes:
            "out" (pmt_pair): PDU with `start_sequence` prepended and
                `tail_sequence` appended to the 8-byte payload (18 bytes
                total); metadata unchanged.

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - payload is not a u8vector (error - same)
            - payload is not exactly 8 bytes (error - same)
            - packing the sequences or publishing fails (error - same; covers a bad start_sequence/tail_sequence value reassigned after construction, which bypasses __init__'s validation)
        """
        if not pmt.is_pair(msg):
            self.logger.error("Input message is not a pair.")
            return

        # unpack PDU to get meta and body
        meta = pmt.car(msg)
        body_pmt = pmt.cdr(msg)

        if not pmt.is_u8vector(body_pmt):
            self.logger.error("Input message body is not a PDU.")
            return

        # Full body from here on wrapped in catch-log-drop, including the
        # final publish - a raise anywhere in here (e.g. struct.pack on a
        # runtime-reassigned out-of-range sequence value) must never
        # escape this handler.
        try:
            # Convert body to bytes for easy concatenation
            payload_bytes = bytes(pmt.u8vector_elements(body_pmt))

            # Check payload size
            if len(payload_bytes) != PAYLOAD_SIZE_BYTES:
                self.logger.error(f"Payload size is {len(payload_bytes)} bytes, expected {PAYLOAD_SIZE_BYTES} bytes")
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
            self.logger.debug("OK")

            # finds last codeword of an message => Message is done
            if pmt.dict_has_key(meta, pmt.intern("filled")):
                self.logger.info("OK\n")
        except Exception as exc:
            self.logger.error(f"Failed to frame or publish message: {exc}")
            return
