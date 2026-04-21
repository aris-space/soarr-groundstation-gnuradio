#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from construct import BitsInteger, If, Rebuild, this, Switch, Struct, BitStruct, BitsInteger as Bits, Int8ub, Int16ub, Int32ub, Int64ub, Padding, Bytes
from gnuradio import gr
import pmt

class ccsdsReader(gr.basic_block):
    """
    docstring for block ccsdsReader
    """
    def __init__(self, message_type:int=0, sdls_type:int=3, encapsulation_used:bool=True, data_type:int=0):
        gr.basic_block.__init__(self,
            name="CCSDS Reader",
            in_sig=None,
            out_sig=None)

        self.message_type = message_type
        self.sdls_type = sdls_type
        self.encapsulation_used = encapsulation_used
        self.data_type = data_type

        # Register message ports
        self.message_port_register_in(pmt.intern("ccsds"))
        self.message_port_register_out(pmt.intern("debug"))

        # Set message handler for incoming messages on the "ccsds" port
        self.set_msg_handler(pmt.intern("ccsds"), self.decode_ccsds)



    def csp_header(self):
        """
        LibCSP 2.x Header according to CCSDS standards
        Total: 6 bytes (48 bits)
        """
        return BitStruct(
            "priority" / Bits(2),                # Priority (2 bits)
            "source" / Bits(14),                 # Source Address (14 bits)
            "destination" / Bits(14),            # Destination Address (14 bits)
            "dest_port" / Bits(6),               # Destination Port (6 bits)
            "source_port" / Bits(6),             # Source Port (6 bits)
            "flags" / Bits(6),                   # Flags including HMAC, XTEA, RDP, CRC (6 bits)
        )

    def encapsulation_header(self):
        """
        Encapsulation Packet Protocol Header
        Total: 1-8 bytes (variable length based on Length of Length field)
        """
        return BitStruct(
            "packet_version" / Bits(3),          # Packet Version Number (3 bits)
            "protocol_id" / Bits(3),             # Protocol ID (3 bits): 0b000=idle, 0b111=mission-specific
            "length_of_length" / Bits(2),        # Length of Length field (2 bits) - defines packet length size
            "user_defined_field" / Bits(4),      # User Defined Field (4 bits) - optional based on mission
            "protocol_id_extension" / Bits(4),   # Protocol ID Extension (4 bits) - only if protocol_id is 0b110
            "ccsds_defined_field" / Bits(16),    # CCSDS Defined Field (16 bits) - reserved/mission-specific
            "packet_length" / Bits(16),          # Packet Length field (variable, 16-bit example)
        )

    def sdls_security_header(self):
        """
        SDLS Security Header according to CCSDS 355.0-B-1
        Total: 4 bytes minimum (32 bits)
        """
        return BitStruct(
            "security_param_index" / Bits(16),   # Security Parameter Index (16 bits)
            "initialization_vector" / Bits(16),  # Initialization Vector (16 bits minimum, can be 8 or 16)
        )

    def sdls_security_trailer(self):
        """
        SDLS Security Trailer with Message Authentication Code (MAC)
        Total: 32 bytes (256 bits)
        """
        return Bytes(32)  # Message Authentication Code (256 bits / 32 bytes)

    def tc_header(self):
        """
        Transfer Frame Primary Header (TFPH) according to CCSDS 232.0-B-4
        Total: 5 bytes (40 bits)
        """
        return BitStruct(
            "tfvn" / Bits(2),                    # Transfer Frame Version Number (2 bits)
            "bypass_flag" / Bits(1),             # Bypass Flag (1 bit): 0b0=Type-A, 0b1=Type-B
            "control_flag" / Bits(1),            # Control Flag (1 bit): 0b0=Type-D, 0b1=Type-C
            "reserve" / Bits(2),                 # Reserved for future use (2 bits)
            "scid" / Bits(10),                   # Spacecraft Identifier (10 bits)
            "vcid" / Bits(6),                    # Virtual Channel Identification (6 bits)
            "frame_length" / Bits(10),           # Frame Length in bytes including TFPH and Frame Error Control Field (10 bits)
            "fsn" / Bits(8),                     # Frame Sequence Number (8 bits)
        )

    def frame_error_control_field(self):
        """
        Frame Error Control Field (FECF)
        CRC error detection for the frame
        Total: 2 bytes (16 bits)
        """
        return Int16ub

    def ccsds_message(self):
        """
        Complete CCSDS message structure with configurable headers based on decode flags
        Includes:
        - TC Header (always present for TC message type)
        - CSP Header (if data_type is 1)
        - Encapsulation Header (if encapsulation_used is True)
        - SDLS Security Header (if sdls_type is not 0)
        - Data payload (GreedyBytes)
        - SDLS Security Trailer (if sdls_type is not 0)
        - Frame Error Control Field (always present)
        
        Parameters:
            message_type: 0=TC, 1=TM (currently only TC supported)
            sdls_type: 0=No SDLS, 1=Encryption only, 2=Authentication only, 3=Both
            encapsulation_used: True/False for encapsulation header
            data_type: 0=Raw, 1=CSP
        """
        fixed_overhead = 5 + 2  # TC primary header + FECF
        if self.data_type == 1:
            fixed_overhead += 6  # CSP header
        if self.encapsulation_used:
            fixed_overhead += 6  # Encapsulation header (as implemented)
        if self.sdls_type != 0:
            fixed_overhead += 4 + 32  # SDLS header + SDLS trailer

        data_field = Bytes(lambda ctx: max(int(ctx.tc_header.frame_length) - fixed_overhead, 0))

        # Build message structure based on configuration flags
        if self.data_type == 1 and self.encapsulation_used and self.sdls_type != 0:
            # Full structure with CSP, encapsulation, and SDLS
            return Struct(
                "tc_header" / self.tc_header(),
                "csp_header" / self.csp_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "data" / data_field,
                "sdls_security_trailer" / self.sdls_security_trailer(),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.data_type == 1 and self.encapsulation_used:
            # CSP and encapsulation, no SDLS
            return Struct(
                "tc_header" / self.tc_header(),
                "csp_header" / self.csp_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "data" / data_field,
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.data_type == 1 and self.sdls_type != 0:
            # CSP and SDLS, no encapsulation
            return Struct(
                "tc_header" / self.tc_header(),
                "csp_header" / self.csp_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "data" / data_field,
                "sdls_security_trailer" / self.sdls_security_trailer(),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.encapsulation_used and self.sdls_type != 0:
            # Encapsulation and SDLS, no CSP
            return Struct(
                "tc_header" / self.tc_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "data" / data_field,
                "sdls_security_trailer" / self.sdls_security_trailer(),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.data_type == 1:
            # Only CSP, no encapsulation or SDLS
            return Struct(
                "tc_header" / self.tc_header(),
                "csp_header" / self.csp_header(),
                "data" / data_field,
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.encapsulation_used:
            # Only encapsulation, no CSP or SDLS
            return Struct(
                "tc_header" / self.tc_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "data" / data_field,
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.sdls_type != 0:
            # Only SDLS, no CSP or encapsulation
            return Struct(
                "tc_header" / self.tc_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "data" / data_field,
                "sdls_security_trailer" / self.sdls_security_trailer(),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        else:
            # Minimal structure: only TC header, data, and FECF
            return Struct(
                "tc_header" / self.tc_header(),
                "data" / data_field,
                "frame_error_control_field" / self.frame_error_control_field(),
            )

    def _python_to_pmt(self, value):
        if isinstance(value, bool):
            return pmt.from_bool(value)
        if isinstance(value, int):
            return pmt.from_long(value)
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            return pmt.init_u8vector(len(b), list(b))
        if isinstance(value, str):
            return pmt.intern(value)
        if isinstance(value, dict):
            out = pmt.make_dict()
            for key, subval in value.items():
                if str(key).startswith("_"):
                    continue
                out = pmt.dict_add(out, pmt.intern(str(key)), self._python_to_pmt(subval))
            return out
        if hasattr(value, "items"):
            out = pmt.make_dict()
            for key, subval in value.items():
                if str(key).startswith("_"):
                    continue
                out = pmt.dict_add(out, pmt.intern(str(key)), self._python_to_pmt(subval))
            return out
        return pmt.intern(str(value))

    def decode_ccsds(self, msg):
        if not pmt.is_pair(msg):
            return

        in_meta = pmt.car(msg)
        in_body = pmt.cdr(msg)
        if not pmt.is_u8vector(in_body):
            return

        frame_bytes = bytes(pmt.u8vector_elements(in_body))

        try:
            parsed = self.ccsds_message().parse(frame_bytes)
        except Exception as exc:
            self.logger.error(f"Failed to parse CCSDS frame: {exc}")
            return

        out_meta = in_meta if pmt.is_dict(in_meta) else pmt.make_dict()
        for key, value in parsed.items():
            if str(key).startswith("_"):
                continue
            out_meta = pmt.dict_add(out_meta, pmt.intern(str(key)), self._python_to_pmt(value))

        out_body = pmt.init_u8vector(len(frame_bytes), list(frame_bytes))
        self.message_port_pub(pmt.intern("debug"), pmt.cons(out_meta, out_body))
