#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from construct import BitsInteger, Computed, If, Rebuild, this, Switch, Struct, BitStruct, BitsInteger as Bits, Int8ub, Int16ub, Int32ub, Int64ub, Padding, Bytes
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
        return Struct(
            "first_octet" / Int8ub,
            "packet_version" / Computed(lambda ctx: (ctx.first_octet >> 5) & 0b111),
            "protocol_id" / Computed(lambda ctx: (ctx.first_octet >> 2) & 0b111),
            "length_of_length" / Computed(lambda ctx: ctx.first_octet & 0b11),
            "_user_defined_field_raw" / If(this.length_of_length >= 0b10, Int8ub),
            "user_defined_field" / Computed(
                lambda ctx: (ctx._user_defined_field_raw >> 4) & 0b1111
                if ctx._user_defined_field_raw is not None
                else None
            ),
            "protocol_id_extension" / Computed(
                lambda ctx: ctx._user_defined_field_raw & 0b1111
                if ctx._user_defined_field_raw is not None
                else None
            ),
            "ccsds_defined_field" / If(this.length_of_length >= 0b11, Int16ub),
            "packet_length" / If(
                this.length_of_length != 0b00,
                Switch(this.length_of_length, {
                    0b01: Int8ub,
                    0b10: Int16ub,
                    0b11: Int32ub,
                }),
            ),
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
        Total: 16 bytes (128 bits)
        """
        return Bytes(16)  # Message Authentication Code (128 bits / 16 bytes)

    def _encapsulation_header_length(self, encap_header) -> int:
        if encap_header is None:
            return 0

        if isinstance(encap_header, dict):
            length_of_length = int(encap_header.get("length_of_length", 0))
        else:
            length_of_length = int(encap_header.length_of_length)
        length_bytes = {0b01: 1, 0b10: 2, 0b11: 4}.get(length_of_length, 0)
        header_bytes = 1  # First octet
        if length_of_length >= 0b10:
            header_bytes += 1  # user_defined_field + protocol_id_extension
        if length_of_length >= 0b11:
            header_bytes += 2  # ccsds_defined_field
        header_bytes += length_bytes
        return header_bytes

    def _sdls_header_length(self) -> int:
        return 4  # SPI (2 bytes) + IV (2 bytes)

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

    def ccsds_message(self, encapsulation_used=None):
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
        encapsulation_used = self.encapsulation_used if encapsulation_used is None else encapsulation_used

        def _data_length(ctx):
            total_length = int(ctx.tc_header.frame_length) + 1
            overhead = 5 + 2  # TC primary header + FECF
            if self.data_type == 1:
                overhead += 6  # CSP header
            if encapsulation_used:
                overhead += self._encapsulation_header_length(ctx.encapsulation_header)
            if self.sdls_type != 0:
                overhead += self._sdls_header_length()
            if self.sdls_type in (2, 3):
                overhead += 16
            return max(total_length - overhead, 0)

        data_field = Bytes(_data_length)

        # Build message structure based on configuration flags

        if self.data_type == 1 and encapsulation_used and self.sdls_type != 0:
            # Full structure with CSP, SDLS, and encapsulation
            return Struct(
                "tc_header" / self.tc_header(),
                "csp_header" / self.csp_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "data" / data_field,
                "sdls_security_trailer" / If(self.sdls_type in (2, 3), self.sdls_security_trailer()),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif self.data_type == 1 and encapsulation_used:
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
                "sdls_security_trailer" / If(self.sdls_type in (2, 3), self.sdls_security_trailer()),
                "frame_error_control_field" / self.frame_error_control_field(),
            )
        elif encapsulation_used and self.sdls_type != 0:
            # SDLS and encapsulation, no CSP
            return Struct(
                "tc_header" / self.tc_header(),
                "sdls_security_header" / self.sdls_security_header(),
                "encapsulation_header" / self.encapsulation_header(),
                "data" / data_field,
                "sdls_security_trailer" / If(self.sdls_type in (2, 3), self.sdls_security_trailer()),
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
        elif encapsulation_used:
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
                "sdls_security_trailer" / If(self.sdls_type in (2, 3), self.sdls_security_trailer()),
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

        total_length = int(parsed.tc_header.frame_length) + 1
        encap_len = self._encapsulation_header_length(getattr(parsed, "encapsulation_header", None))
        sdls_header_len = self._sdls_header_length() if self.sdls_type != 0 else 0
        sdls_trailer_len = 16 if self.sdls_type in (2, 3) else 0
        data_len = len(parsed.data) if hasattr(parsed, "data") and parsed.data is not None else 0
        overhead = 5 + 2 + (6 if self.data_type == 1 else 0) + encap_len + sdls_header_len + sdls_trailer_len
        self.logger.info(
            f"Parsed frame lengths: total={total_length} overhead={overhead} data={data_len} "
            f"encap={encap_len} sdls_hdr={sdls_header_len} sdls_trailer={sdls_trailer_len}"
        )

        out_meta = in_meta if pmt.is_dict(in_meta) else pmt.make_dict()

        if hasattr(parsed, "encapsulation_header") and parsed.encapsulation_header is not None:
            encaps_meta = pmt.make_dict()
            for key, value in parsed.encapsulation_header.items():
                if str(key).startswith("_"):
                    continue
                encaps_meta = pmt.dict_add(encaps_meta, pmt.intern(str(key)), self._python_to_pmt(value))
            out_meta = pmt.dict_add(out_meta, pmt.intern("encapsulation_header"), encaps_meta)

        sdls_meta = pmt.make_dict()
        sdls_header = pmt.make_dict()
        if hasattr(parsed, "sdls_security_header") and parsed.sdls_security_header is not None:
            sdls_header = pmt.dict_add(
                sdls_header,
                pmt.intern("initialization_vector"),
                pmt.from_long(int(parsed.sdls_security_header.initialization_vector)),
            )
            sdls_header = pmt.dict_add(
                sdls_header,
                pmt.intern("sdls_counter"),
                pmt.from_long(int(parsed.sdls_security_header.initialization_vector)),
            )
            sdls_header = pmt.dict_add(
                sdls_header,
                pmt.intern("security_param_index"),
                pmt.from_long(int(parsed.sdls_security_header.security_param_index)),
            )
            sdls_header = pmt.dict_add(
                sdls_header,
                pmt.intern("spi"),
                pmt.from_long(int(parsed.sdls_security_header.security_param_index)),
            )
        sdls_meta = pmt.dict_add(sdls_meta, pmt.intern("security_header"), sdls_header)

        if hasattr(parsed, "sdls_security_trailer") and parsed.sdls_security_trailer is not None:
            sdls_meta = pmt.dict_add(
                sdls_meta,
                pmt.intern("security_trailer"),
                self._python_to_pmt(parsed.sdls_security_trailer),
            )
        out_meta = pmt.dict_add(out_meta, pmt.intern("sdls"), sdls_meta)

        telecommand_meta = pmt.make_dict()
        if hasattr(parsed, "frame_error_control_field") and parsed.frame_error_control_field is not None:
            telecommand_meta = pmt.dict_add(
                telecommand_meta,
                pmt.intern("frame_error_control_field"),
                pmt.from_long(int(parsed.frame_error_control_field)),
            )

        tc_header_meta = pmt.make_dict()
        if hasattr(parsed, "tc_header") and parsed.tc_header is not None:
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("tfvn"), pmt.from_long(int(parsed.tc_header.tfvn)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("bypass_flag"), pmt.from_long(int(parsed.tc_header.bypass_flag)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("control_flag"), pmt.from_long(int(parsed.tc_header.control_flag)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("reserve"), pmt.from_long(int(parsed.tc_header.reserve)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("scid"), pmt.from_long(int(parsed.tc_header.scid)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("vcid"), pmt.from_long(int(parsed.tc_header.vcid)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("vcid_counter"), pmt.from_long(int(parsed.tc_header.fsn)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("fsn"), pmt.from_long(int(parsed.tc_header.fsn)))
            tc_header_meta = pmt.dict_add(tc_header_meta, pmt.intern("frame_length"), pmt.from_long(int(parsed.tc_header.frame_length)))
        telecommand_meta = pmt.dict_add(telecommand_meta, pmt.intern("tc_header"), tc_header_meta)
        out_meta = pmt.dict_add(out_meta, pmt.intern("telecommand"), telecommand_meta)

        payload_bytes = bytes(parsed.data) if hasattr(parsed, "data") and parsed.data is not None else b""
        out_body = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))
        self.message_port_pub(pmt.intern("debug"), pmt.cons(out_meta, out_body))
