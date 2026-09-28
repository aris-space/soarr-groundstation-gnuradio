#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

BYTE_MIN = 0x00
BYTE_MAX = 0xFF


class cltu_burst_builder(gr.basic_block):
    """
    Wrap each CLTU into one transmission burst for CCSDS 231.0-B PLOP-1
    operation (carrier on only while transmitting): acquisition sequence,
    CLTU, short idle tail.

    Flow:
    - Receive a CLTU PDU on `in` (from cltu_framer)
    - Publish `acquisition sequence || CLTU || idle tail` as one PDU on `out`

    Feed `out` into GNU Radio's PDU to Tagged Stream block: the stream then
    carries only bursts, each with a length tag for the USRP sink, and
    nothing between them - so no idle fill queues up in the stream buffers
    ahead of the next CLTU, unlike acquisition_idle_sequencer's continuous
    (PLOP-2) stream.
    """

    def __init__(self, acquisition_length:int=64, tail_length:int=4, fill_byte:int=0xAA, diff_encoded:bool=False):
        """
        Args:
            acquisition_length (int): acquisition sequence length in bytes,
                sent before every CLTU so the receiver can lock on; size it
                to the receiver's acquisition time (0 = none).
            tail_length (int): idle bytes sent after the CLTU, letting the
                modulator's filter flush the last symbols (0 = none).
            fill_byte (int): byte used for the acquisition sequence and the
                tail. 0xAA gives alternating bits.
            diff_encoded (bool): if True, use 0xFF instead of fill_byte, which
                a differential modulator turns into alternating symbols.

        Raises:
            ValueError: acquisition_length or tail_length is negative, or
                fill_byte is outside 0-255.
        """
        gr.basic_block.__init__(self,
            name="cltu_burst_builder",
            in_sig=None,
            out_sig=None)

        if acquisition_length < 0:
            raise ValueError(f"acquisition_length must be >= 0, got {acquisition_length}.")
        if tail_length < 0:
            raise ValueError(f"tail_length must be >= 0, got {tail_length}.")
        if not (BYTE_MIN <= fill_byte <= BYTE_MAX):
            raise ValueError(f"fill_byte must be a single byte ({BYTE_MIN}-{BYTE_MAX}), got {fill_byte}.")

        fill = 0xFF if diff_encoded else fill_byte
        self._acquisition = bytes([fill]) * int(acquisition_length)
        self._tail = bytes([fill]) * int(tail_length)

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.build_burst)

    def build_burst(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload, one
                CLTU.

        Publishes:
            "out" (pmt_pair): PDU with the same metadata and payload
                `acquisition sequence || CLTU || idle tail`.

        Drops when:
            - msg is not a PDU pair, or payload is not a u8vector (error - malformed input at the TX boundary, not raw RF noise)
            - payload is empty (error - same)
            - building or publishing the burst fails (error - same)
        """
        try:
            if not pmt.is_pair(msg):
                self.logger.error("Input message is not a pair.")
                return
            body = pmt.cdr(msg)
            if not pmt.is_u8vector(body):
                self.logger.error("Input message body is not a u8vector.")
                return
            cltu = bytes(pmt.u8vector_elements(body))
            if not cltu:
                self.logger.error("Received empty CLTU; nothing to send.")
                return

            burst = self._acquisition + cltu + self._tail
            self.message_port_pub(pmt.intern("out"), pmt.cons(pmt.car(msg), pmt.init_u8vector(len(burst), list(burst))))
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build or publish burst: {exc}")
