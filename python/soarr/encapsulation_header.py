#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from collections import namedtuple

from construct import BitStruct, BitsInteger, GreedyBytes, If, Rebuild, this, Switch, Struct
from gnuradio import gr
import pmt


PACKET_VERSION_NUMBER = 0b111
PROTOCOL_ID_IDLE = 0b000
PROTOCOL_ID_DATA = 0b111
PROTOCOL_ID_EXTENSION = 0b0000
CCSDS_DEFINED_FIELD = 0b0000_0000_0000_0000

_LengthOfLengthInfo = namedtuple(
    "_LengthOfLengthInfo", ["max_length", "header_length", "packet_length_width"]
)


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

    # CCSDS 133.1-B Table 4-2: length-of-length (LOL) code -> max
    # encapsulated data field size (bytes), header length (bytes), and
    # packet_length field width (bits, or None where LOL has no
    # packet_length field). Single source of truth for header-variant
    # selection, packet_length sizing, and the packet_length field's bit
    # width - previously duplicated across three separate places in this
    # module. A class attribute (not module-level) so it's reachable via
    # the imported class name, same as _determine_length_of_length.
    LENGTH_OF_LENGTH_TABLE = {
        0b00: _LengthOfLengthInfo(max_length=0, header_length=1, packet_length_width=None),
        0b01: _LengthOfLengthInfo(max_length=2**8 - 3, header_length=2, packet_length_width=8),
        0b10: _LengthOfLengthInfo(max_length=2**16 - 5, header_length=4, packet_length_width=16),
        0b11: _LengthOfLengthInfo(max_length=2**32 - 9, header_length=8, packet_length_width=32),
    }

    # Built once at class-definition time, not per message: this Struct is
    # a stateless schema (construct.Struct.build() takes a fresh context
    # per call), so rebuilding it on every add_header() call was pure waste.
    _PACKET_FORMAT = Struct(
        "header" / BitStruct(
            # First octet: version (3), protocol id (3), and length-of-length (2)
            "packet_version_number" / BitsInteger(3),
            "protocol_id" / BitsInteger(3),
            "length_of_length" / Rebuild(
                BitsInteger(2),
                lambda ctx: encapsulation_header._determine_length_of_length(ctx._.payload),
            ),
            # Optional fields present only for longer header variants.
            "user_defined_field" / If(this.length_of_length >= 0b10, BitsInteger(4)),
            "protocol_id_extension" / If(this.length_of_length >= 0b10, BitsInteger(4)),
            "ccsds_defined_field" / If(this.length_of_length >= 0b11, BitsInteger(16)),
            # Packet length field width depends on length_of_length code.
            "packet_length" / If(
                this.length_of_length != 0b00,
                Switch(this.length_of_length, {
                    lol: BitsInteger(info.packet_length_width)
                    for lol, info in LENGTH_OF_LENGTH_TABLE.items()
                    if info.packet_length_width is not None
                }),
            ),
        ),
        "payload" / GreedyBytes,
    )

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

    @staticmethod
    def _determine_length_of_length(obj) -> int:
        """
        Determine the 2-bit LENGTH_OF_LENGTH code from encapsulated data length.

        Thresholds use maximum encapsulated-data-field sizes from Table 4-2:
        - 00: no length field (empty encapsulated data field)
        - 01: 1-byte packet length field
        - 10: 2-byte packet length field
        - 11: 4-byte packet length field

        Raises:
            ValueError: length exceeds every LOL variant's maximum.
        """
        length = len(obj)  # in bytes
        for lol, info in encapsulation_header.LENGTH_OF_LENGTH_TABLE.items():
            if length <= info.max_length:
                return lol

        raise ValueError("Object too large to encode with SDLS header.")

    def add_header(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.

        Publishes:
            "out" (pmt_pair): PDU with unchanged metadata and the payload
                prefixed by a CCSDS 133.1-B Encapsulation Packet header,
                whose variant is selected by payload size (see
                _determine_length_of_length).

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - header packing or publishing fails, e.g. payload too large to encode (error - this block isn't directly exposed to raw RF data, so a failure here isn't plausibly channel noise)
        """
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

        # Full body from here on wrapped in catch-log-drop, including the
        # final publish call - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
            payload_bytes = bytes(pmt.u8vector_elements(payload))

            protocol_id = PROTOCOL_ID_IDLE if payload_bytes == b'' else PROTOCOL_ID_DATA
            if protocol_id == PROTOCOL_ID_IDLE:
                self.logger.info("Received empty payload; packing encapsulation header as idle packet")

            # packet_length encodes total packet length (header + encapsulated data field).
            length_of_length = encapsulation_header._determine_length_of_length(payload_bytes)
            info = encapsulation_header.LENGTH_OF_LENGTH_TABLE[length_of_length]
            packet_length_value = None if length_of_length == 0b00 else len(payload_bytes) + info.header_length

            msg_out = encapsulation_header._PACKET_FORMAT.build({
                "header": {
                    "packet_version_number": PACKET_VERSION_NUMBER,
                    "protocol_id": protocol_id,
                    # length_of_length is computed by Rebuild from the payload.
                    "user_defined_field": self.user_defined_field,
                    "protocol_id_extension": PROTOCOL_ID_EXTENSION,
                    "ccsds_defined_field": CCSDS_DEFINED_FIELD,
                    "packet_length": packet_length_value,
                },
                "payload": payload_bytes
            })

            out_msg = pmt.cons(dict_msg, pmt.init_u8vector(len(msg_out), list(msg_out)))
            self.message_port_pub(pmt.intern("out"), out_msg)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build or publish encapsulation header: {exc}")
            return
