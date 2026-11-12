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
BURST_MORE = pmt.intern("burst_more")


class cltu_burst_builder(gr.basic_block):
    """
    Wrap each PDU into one transmission burst (carrier on only while
    transmitting): acquisition sequence, the PDU, short idle tail. On TC the
    PDU is a CLTU (CCSDS 231.0-B PLOP-1), on TM a CADU or several.

    Flow:
    - Receive a PDU on `in` (a CLTU from cltu_framer, a CADU from
      tm_cadu_framer)
    - Hold it if its metadata has `burst_more` = True (set by
      tm_frame_generator on every frame of a packet but the last)
    - Otherwise publish `acquisition sequence || held PDUs || PDU || idle
      tail` as one PDU on `out` - without the marker, one burst per PDU

    Feed `out` into GNU Radio's PDU to Tagged Stream block: the stream then
    carries only bursts, each with a length tag for the USRP sink, and
    nothing between them - so no idle fill queues up in the stream buffers
    ahead of the next PDU, unlike acquisition_idle_sequencer's continuous
    (PLOP-2) stream. The acquisition sequence and tail are physical-layer
    fill, outside the CLTU or CADU itself.
    """

    def __init__(self, acquisition_length:int=64, tail_length:int=4, fill_byte:int=0xAA, diff_encoded:bool=False,
                 max_burst_pdus:int=64):
        """
        Args:
            acquisition_length (int): acquisition sequence length in bytes,
                sent before every burst so the receiver can lock on; size it
                to the receiver's acquisition time (0 = none).
            tail_length (int): idle bytes sent after the PDU, letting the
                modulator's filter flush the last symbols (0 = none).
            fill_byte (int): byte used for the acquisition sequence and the
                tail. 0xAA gives alternating bits.
            diff_encoded (bool): if True, use 0xFF instead of fill_byte, which
                a differential modulator turns into alternating symbols.
            max_burst_pdus (int): most PDUs in one burst; reached with
                burst_more still set, the held PDUs go out as a burst
                (logged at warn) and the rest start the next one.

        Raises:
            ValueError: acquisition_length or tail_length is negative,
                fill_byte is outside 0-255, or max_burst_pdus is below 1.
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
        if max_burst_pdus < 1:
            raise ValueError(f"max_burst_pdus must be >= 1, got {max_burst_pdus}.")

        fill = 0xFF if diff_encoded else fill_byte
        self._acquisition = bytes([fill]) * int(acquisition_length)
        self._tail = bytes([fill]) * int(tail_length)
        self.max_burst_pdus = int(max_burst_pdus)
        self._held = []  # PDU payloads of the burst so far

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.build_burst)

    def build_burst(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata and u8vector payload, one CLTU
                or CADU. Metadata `burst_more` = True: more PDUs of this
                burst follow, hold this one.

        Publishes:
            "out" (pmt_pair): once a PDU without `burst_more` = True
                arrives (or max_burst_pdus are held): payload
                `acquisition sequence || held PDUs || PDU || idle tail`,
                with that PDU's metadata, `burst_more` removed. A PDU
                without the marker and nothing held gives a burst of its
                own with its metadata unchanged.

        Drops when:
            - msg is not a PDU pair, or payload is not a u8vector (error - malformed input at the TX boundary, not raw RF noise); held PDUs stay held
            - payload is empty (error - same); held PDUs stay held
            - building or publishing the burst fails (error - same); the held PDUs are lost with it
        """
        try:
            if not pmt.is_pair(msg):
                self.logger.error("Input message is not a pair.")
                return
            body = pmt.cdr(msg)
            if not pmt.is_u8vector(body):
                self.logger.error("Input message body is not a u8vector.")
                return
            data = bytes(pmt.u8vector_elements(body))
            if not data:
                self.logger.error("Received empty PDU; nothing to send.")
                return

            meta = pmt.car(msg)
            more = pmt.is_dict(meta) and pmt.eq(pmt.dict_ref(meta, BURST_MORE, pmt.PMT_F), pmt.PMT_T)
            self._held.append(data)
            if more and len(self._held) < self.max_burst_pdus:
                return
            if more:
                self.logger.warn(f"{self.max_burst_pdus} PDUs held with burst_more still set; "
                                 "sending them as one burst, the rest follow in the next.")
            if pmt.is_dict(meta) and pmt.dict_has_key(meta, BURST_MORE):
                meta = pmt.dict_delete(meta, BURST_MORE)

            burst = self._acquisition + b"".join(self._held) + self._tail
            self._held = []
            self.message_port_pub(pmt.intern("out"), pmt.cons(meta, pmt.init_u8vector(len(burst), list(burst))))
            self.logger.info("OK")
        except Exception as exc:
            self._held = []
            self.logger.error(f"Failed to build or publish burst: {exc}")
