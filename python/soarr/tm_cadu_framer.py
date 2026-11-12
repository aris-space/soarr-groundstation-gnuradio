#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

# CCSDS 131.0-B-5 9.3 and 9.6: the Attached Sync Markers, in hexadecimal,
# first transmitted bit = most significant bit of the first byte.
ASM_32 = "1ACFFC1D"                    # uncoded, convolutional, Reed-Solomon, concatenated, LDPC 7/8, SMTF LDPC
ASM_64 = "034776C7272895B0"            # Turbo 1/2; LDPC 1/2, 2/3, 4/5 (of a Transfer Frame)
ASM_96 = "25D5C0CE8990F6C9461BF79C"    # Turbo 1/3
ASM_128 = "034776C7272895B0FCB88938D8D76A4F"                   # Turbo 1/4
ASM_192 = "25D5C0CE8990F6C9461BF79CDA2A3F31766F0936B9E40863"   # Turbo 1/6
ASM_EMBEDDED = "352EF853"              # an embedded data stream (9.6)
ASMS = (ASM_32, ASM_64, ASM_96, ASM_128, ASM_192, ASM_EMBEDDED)


class tm_cadu_framer(gr.basic_block):
    """
    Build a CCSDS TM Channel Access Data Unit (131.0-B-5 section 9): prepend
    the Attached Sync Marker to a codeblock, codeword or Transfer Frame.

    The CADU is the ASM and the codeblock and nothing else - CCSDS allows no
    bits between consecutive CADUs (9.4.2), so a preamble or tail for a burst
    comes from the Burst Builder (cltu_burst_builder), not from here.
    """

    def __init__(self, asm: str = ASM_32, length_check: bool = False, codeblock_length: int = 255):
        """
        Args:
            asm (str): the ASM in hexadecimal, one of the CCSDS markers in
                ASMS, chosen by the coding: ASM_32 (default) for uncoded,
                convolutional, Reed-Solomon, concatenated and rate-7/8 LDPC
                data, the longer ones for Turbo and other LDPC rates,
                ASM_EMBEDDED for an embedded data stream.
            length_check (bool): if True, drop PDUs that aren't
                codeblock_length bytes long.
            codeblock_length (int): the expected length in bytes, e.g.
                255 * interleaving depth for Reed-Solomon; only used with
                length_check.

        Raises:
            ValueError: asm is not one of the CCSDS markers, or
                length_check is on with a codeblock_length below 1.
        """
        gr.basic_block.__init__(self, name="TM CADU Framer", in_sig=None, out_sig=None)

        if str(asm).upper() not in ASMS:
            raise ValueError(f"asm must be one of the CCSDS ASMs {ASMS}, got {asm!r}.")
        if length_check and codeblock_length < 1:
            raise ValueError(f"codeblock_length must be at least 1 byte, got {codeblock_length}.")
        self.asm = str(asm).upper()
        self._asm_bytes = bytes.fromhex(self.asm)
        self.length_check = bool(length_check)
        self.codeblock_length = int(codeblock_length)

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.add_asm)

    def add_asm(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload, one
                (randomized) codeblock, codeword or Transfer Frame.

        Publishes:
            "out" (pmt_pair): PDU with the same metadata and payload
                `ASM || codeblock` - one CADU.

        Drops when:
            - msg is not a pair, or its payload is not a u8vector (error - malformed input at the TX boundary, not raw RF noise)
            - the payload is empty (error - same, nothing to frame)
            - length_check is on and the payload is not codeblock_length bytes (error - same)
            - framing or publishing fails (error - same)
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.error("Input is not a PDU with a u8vector payload; dropped.")
                return
            codeblock = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            if not codeblock:
                self.logger.error("Received an empty PDU; nothing to frame.")
                return
            if self.length_check and len(codeblock) != self.codeblock_length:
                self.logger.error(f"Dropped a {len(codeblock)}-byte PDU; expected {self.codeblock_length} bytes.")
                return
            cadu = self._asm_bytes + codeblock
            self.message_port_pub(pmt.intern("out"), pmt.cons(pmt.car(msg), pmt.init_u8vector(len(cadu), list(cadu))))
        except Exception as exc:
            self.logger.error(f"Failed to frame or publish CADU: {exc}")
