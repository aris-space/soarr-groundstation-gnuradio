#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from . import encapsulation_packet


class encapsulation_header(gr.basic_block):
    """
    Build and prepend a CCSDS encapsulation packet header to incoming PDUs.

    The block expects GNU Radio message PDUs in the form:
        (metadata_dict . payload_u8vector)

    The emitted payload is:
        encapsulation_header || original_payload

    Header variant and packet-length field width are selected from payload length
    according to CCSDS 133.1-B (Table 4-2).
    """

    # CCSDS 133.1-B Table 4-2 (length-of-length code -> max data length,
    # header length, packet length field width in bytes), defined in
    # encapsulation_packet; exposed on the block for its callers.
    LENGTH_OF_LENGTH_TABLE = encapsulation_packet.LENGTH_OF_LENGTH_TABLE

    def __init__(self, user_defined_field:int = 0b0000):
        """
        Args:
            user_defined_field (int): 4-bit value packed into the header
                when payload size selects a header variant that includes it
                (see add_header). Only used at message-handling time, but
                validated here so a bad value fails at flowgraph-build time
                rather than depending on payload size to surface it.

        Raises:
            ValueError: user_defined_field is outside the 4-bit range
                (0-15).
        """
        gr.basic_block.__init__(self,
            name="encapsulation_header",
            in_sig=None,
            out_sig=None)

        if not (0 <= user_defined_field <= 0b1111):
            raise ValueError(
                f"user_defined_field must be a 4-bit value (0-15), got {user_defined_field}."
            )

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Handler
        self.set_msg_handler(pmt.intern("in"), self.add_header)

        # Variables
        self.user_defined_field = user_defined_field

    def add_header(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.

        Publishes:
            "out" (pmt_pair): PDU with unchanged metadata and the payload
                prefixed by a CCSDS 133.1-B Encapsulation Packet header,
                whose variant is selected by payload size (see
                encapsulation_packet.header_for_payload).

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - header packing or publishing fails, e.g. payload too large to encode (error - this block isn't directly exposed to raw RF data, so a failure here isn't plausibly channel noise)
        """
        # Full body wrapped in catch-log-drop, including the final
        # publish call - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
            if not pmt.is_pair(msg):
                self.logger.error(f"Received non-PDU message: {msg}")
                return

            dict_msg = pmt.car(msg)
            payload = pmt.cdr(msg)

            if not pmt.is_dict(dict_msg):
                self.logger.error(f"Received PDU with non-dict metadata: {dict_msg}")
                return

            if not pmt.is_u8vector(payload):
                self.logger.error(f"Received PDU with non-u8vector payload: {payload}")
                return

            payload_bytes = bytes(pmt.u8vector_elements(payload))

            if payload_bytes == b"":
                self.logger.info("Received empty payload; packing encapsulation header as idle packet")

            header = encapsulation_packet.header_for_payload(len(payload_bytes), self.user_defined_field)
            msg_out = header + payload_bytes
            out_msg = pmt.cons(dict_msg, pmt.init_u8vector(len(msg_out), list(msg_out)))
            self.message_port_pub(pmt.intern("out"), out_msg)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build or publish encapsulation header: {exc}")
            return
