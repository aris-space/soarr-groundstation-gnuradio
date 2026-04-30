#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from construct import BitStruct, BitsInteger, GreedyBytes, If, Rebuild, this, Switch, Struct
from gnuradio import gr
import pmt


PACKET_VERSION_NUMBER = 0b111
PROTOCOL_ID_IDLE = 0b000
PROTOCOL_ID_DATA = 0b111
PROTOCOL_ID_EXTENTION = 0b0000
CCSDS_DEFINED_FIELD = 0b0000_0000_0000_0000


class encapsulationHeader(gr.basic_block):
    """
    Build and prepend a CCSDS encapsulation packet header to incoming PDUs.

    The block expects GNU Radio message PDUs in the form:
        (metadata_dict . payload_u8vector)

    The emitted payload is:
        encapsulation_header || original_payload

    Header variant and packet-length field width are selected from payload length
    according to CCSDS 133.1-B (Table 4-2).
    """
    def __init__(self, user_defined_field:int = 0b0000):
        gr.basic_block.__init__(self,
            name="encapsulationHeader",
            in_sig=None,
            out_sig=None)
        

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
        """
        length = len(obj) # in bytes
        if length == 0:
            return 0b00  # No extended length field
        
        if length <= 2**8 - 3:
            return 0b01  # 1 byte for length
        
        if length <= 2**16 - 5:
            return 0b10  # 2 bytes for length
        
        if length <= 2**32 - 9:
            return 0b11  # 4 bytes for length
        
        raise ValueError("Object too large to encode with SDLS header.")



    def add_header(self, msg):
        """
        Validate input PDU, build encapsulation header, and publish framed PDU.

        Input:
            msg: PMT pair (dict metadata, u8vector payload)
        Output:
            PMT pair with unchanged metadata and header-prepended payload
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
        
        payload_bytes = bytes(pmt.u8vector_elements(payload))
        if payload_bytes is None:
            self.logger.error("Failed to extract payload bytes.")
            return
        

        protocol_id = PROTOCOL_ID_DATA
        if payload_bytes == b'':
            self.logger.info("Received empty payload; packing encapsulation header as idle packet")
            protocol_id = PROTOCOL_ID_IDLE

        # Determine packet_length field value according to CCSDS table 4-2.
        # packet_length encodes total packet length (header + encapsulated data field).
        length_of_length = encapsulationHeader._determine_length_of_length(payload_bytes)
        if length_of_length == 0b00:
            packet_length_value = None
        elif length_of_length == 0b01:
            packet_length_value = len(payload_bytes) + 2
        elif length_of_length == 0b10:
            packet_length_value = len(payload_bytes) + 4
        else:
            packet_length_value = len(payload_bytes) + 8


        # Build header
        packet_format = Struct(
            "header" / BitStruct(
                # First octet: version (3), protocol id (3), and length-of-length (2)
                "packet_version_number" / BitsInteger(3),
                "protocol_id" / BitsInteger(3),
                "length_of_length" / Rebuild(
                    BitsInteger(2),
                    lambda ctx: encapsulationHeader._determine_length_of_length(ctx._.payload),
                ),
                # Optional fields present only for longer header variants.
                "user_defined_field" / If(this.length_of_length >= 0b10, BitsInteger(4)),
                "protocol_id_extention" / If(this.length_of_length >= 0b10, BitsInteger(4)),
                "ccsds_defined_field" / If(this.length_of_length >= 0b11, BitsInteger(16)),
                # Packet length field width depends on length_of_length code.
                "packet_length" / If(
                    this.length_of_length != 0b00,
                    Switch(this.length_of_length, {
                        0b01: BitsInteger(8),
                        0b10: BitsInteger(16),
                        0b11: BitsInteger(32),
                    }),
                ),
            ),
            "payload" / GreedyBytes,
        )

        # Pack the header
        msg_out = packet_format.build({
            "header": {
                "packet_version_number": PACKET_VERSION_NUMBER,
                "protocol_id": protocol_id,
                # length_of_length is computed by Rebuild from the payload.
                "user_defined_field": self.user_defined_field,
                "protocol_id_extention": PROTOCOL_ID_EXTENTION,
                "ccsds_defined_field": CCSDS_DEFINED_FIELD,
                "packet_length": packet_length_value,
            },
            "payload": payload_bytes
        })


        # Publish the new message with header
        out_msg = pmt.cons(dict_msg, pmt.init_u8vector(len(msg_out), list(msg_out)))
        self.message_port_pub(pmt.intern("out"), out_msg)
        self.logger.debug(f"Encapsulation done")


