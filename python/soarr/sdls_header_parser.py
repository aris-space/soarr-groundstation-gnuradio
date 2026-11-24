#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from .sdls_constants import SDLS_COUNTER_BYTES, SPI_BYTES

HEADER_BYTES = SPI_BYTES + SDLS_COUNTER_BYTES
SDLS = pmt.intern("sdls")
SECURITY_HEADER = pmt.intern("security_header")


class sdls_header_parser(gr.basic_block):
    """
    Read and strip a CCSDS 355.0-B-1 SDLS Security Header (SPI + IV) at the
    start of a PDU's payload, as sdls_header builds it, and put its fields
    into the metadata under `sdls.security_header` - the keys ccsds_reader
    publishes on TC, where the RX Key Database and SDLS Process look for
    them.
    """

    def __init__(self):
        gr.basic_block.__init__(self, name="SDLS Header Parser", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.parse_header)

    def parse_header(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload:
                SPI (2 bytes) || IV (the 2-byte SDLS counter) || data.

        Publishes:
            "out" (pmt_pair): PDU with the input metadata plus
                sdls.security_header:
                    spi, security_param_index (int): the SPI.
                    sdls_counter, initialization_vector (int): the IV.
                Other keys under `sdls` are kept. Payload: the data after
                the header, (encrypted) data and MAC as they are.

        Drops when:
            - msg is not a pair, its metadata not a dict, or its payload not a u8vector (error - after the frame decoder and encapsulation parser checked the structure, not raw RF noise)
            - the payload is shorter than the security header (error - same)
            - parsing or publishing fails (error - same)
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_dict(pmt.car(msg)) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.error("Input is not a PDU with a dict and a u8vector payload; dropped.")
                return
            payload = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            if len(payload) < HEADER_BYTES:
                self.logger.error(f"Payload of {len(payload)} bytes is shorter than the {HEADER_BYTES}-byte "
                                  f"SDLS security header; dropped.")
                return
            spi = int.from_bytes(payload[:SPI_BYTES], "big")
            counter = int.from_bytes(payload[SPI_BYTES:HEADER_BYTES], "big")

            meta = pmt.car(msg)
            sdls = pmt.dict_ref(meta, SDLS, pmt.PMT_NIL)
            sdls = sdls if pmt.is_dict(sdls) and not pmt.eq(sdls, pmt.PMT_NIL) else pmt.make_dict()
            header = pmt.dict_ref(sdls, SECURITY_HEADER, pmt.PMT_NIL)
            header = header if pmt.is_dict(header) and not pmt.eq(header, pmt.PMT_NIL) else pmt.make_dict()
            for key, value in (("spi", spi), ("security_param_index", spi), ("sdls_counter", counter),
                               ("initialization_vector", counter)):
                header = pmt.dict_add(header, pmt.intern(key), pmt.from_long(value))
            meta = pmt.dict_add(meta, SDLS, pmt.dict_add(sdls, SECURITY_HEADER, header))

            data = payload[HEADER_BYTES:]
            self.message_port_pub(pmt.intern("out"), pmt.cons(meta, pmt.init_u8vector(len(data), list(data))))
        except Exception as exc:
            self.logger.error(f"Failed to parse the SDLS security header or publish: {exc}")
