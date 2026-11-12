#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from functools import lru_cache

from gnuradio import gr
import numpy as np
import pmt

# CCSDS 131.0-B-5 section 10: the TM pseudo-random sequences, by LFSR
# register length (as lfsr_scrambler's register_length on the TC side).
LFSR_17 = 17  # h(x) = x^17 + x^14 + 1: the 131071-bit sequence, the current one
LFSR_8 = 8    # h(x) = x^8 + x^7 + x^5 + x^3 + 1: the 255-bit sequence, legacy (10.4.2)

# Per register length: exponents of h(x) below its degree, and the start
# state as written in 10.4.3 - its bits, read right to left, are the first
# bits of the sequence.
_GENERATORS = {
    LFSR_17: ((14, 0), "11000111000111000"),
    LFSR_8: ((7, 5, 3, 0), "11111111"),
}


@lru_cache(maxsize=None)
def sequence_bytes(register_length, nbytes):
    """
    Args:
        register_length (int): LFSR_17 or LFSR_8.
        nbytes (int): how many bytes of the sequence to return.

    Returns:
        bytes: the first nbytes of the sequence, first bit in each byte's
            MSB - the order it is XORed onto a codeblock (10.3.2).
    """
    taps, seed = _GENERATORS[register_length]
    bits = [int(b) for b in reversed(seed)]
    while len(bits) < 8 * nbytes:
        # h(x) as a recurrence: s[n + degree] = XOR of s[n + k], k in taps.
        n = len(bits) - register_length
        bit = 0
        for k in taps:
            bit ^= bits[n + k]
        bits.append(bit)
    return np.packbits(np.array(bits[:8 * nbytes], dtype=np.uint8)).tobytes()


class tm_randomizer(gr.basic_block):
    """
    CCSDS TM Pseudo-Randomizer (131.0-B-5 section 10): XOR each codeblock,
    codeword or Transfer Frame with the standard pseudo-random sequence,
    restarted at the first bit of every PDU.

    XOR is its own inverse, so the same block randomizes on TX (after
    Reed-Solomon/Turbo/LDPC encoding) and derandomizes on RX (right after
    the ASM, before decoding). The ASM itself is never randomized.
    """

    def __init__(self, register_length: int = LFSR_17, length_check: bool = False,
                 codeblock_length: int = 255):
        """
        Args:
            register_length (int): 17 - the 17-bit LFSR, h(x) = x^17 +
                x^14 + 1, a 131071-bit sequence (default, the current
                standard); or 8 - the 8-bit LFSR, h(x) = x^8 + x^7 + x^5 +
                x^3 + 1, a 255-bit sequence, legacy, for backward
                compatibility. The start state follows from it (10.4.3).
            length_check (bool): if True, drop PDUs that aren't
                codeblock_length bytes long.
            codeblock_length (int): the expected length in bytes, e.g.
                255 * interleaving depth for Reed-Solomon; only used with
                length_check.

        Raises:
            ValueError: register_length is not 17 or 8, or length_check is
                on with a codeblock_length below 1.
        """
        gr.basic_block.__init__(self, name="TM Randomizer", in_sig=None, out_sig=None)

        if register_length not in _GENERATORS:
            raise ValueError(f"register_length must be {LFSR_17} or {LFSR_8} bits, got {register_length}.")
        if length_check and codeblock_length < 1:
            raise ValueError(f"codeblock_length must be at least 1 byte, got {codeblock_length}.")
        self.register_length = int(register_length)
        self.length_check = bool(length_check)
        self.codeblock_length = int(codeblock_length)

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

    def handle_msg(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload,
                one codeblock, codeword or Transfer Frame (packed bytes).

        Publishes:
            "out" (pmt_pair): PDU with the same metadata and the payload
                XORed with the pseudo-random sequence; length preserved.

        Drops when:
            - msg is not a pair, or its payload is not a u8vector (warn - on RX this block sees data straight after the ASM, before any structural check)
            - length_check is on and the payload is not codeblock_length bytes (warn - same)
            - randomizing or publishing fails (warn - same)
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.warn("Input is not a PDU with a u8vector payload; dropped.")
                return
            data = np.frombuffer(bytes(pmt.u8vector_elements(pmt.cdr(msg))), dtype=np.uint8)
            if self.length_check and len(data) != self.codeblock_length:
                self.logger.warn(f"Dropped a {len(data)}-byte PDU; expected {self.codeblock_length} bytes.")
                return
            pattern = np.frombuffer(sequence_bytes(self.register_length, len(data)), dtype=np.uint8)
            out = np.bitwise_xor(data, pattern)
            self.message_port_pub(pmt.intern("out"), pmt.cons(pmt.car(msg), pmt.init_u8vector(len(out), out.tolist())))
        except Exception as exc:
            self.logger.warn(f"Failed to randomize or publish: {exc}")
