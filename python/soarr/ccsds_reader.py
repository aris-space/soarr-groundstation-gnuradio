#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from construct import If, Struct, BitStruct, BitsInteger as Bits, Int16ub, Bytes
from gnuradio import gr
import pmt

from . import encapsulation_packet

class ccsds_reader(gr.basic_block):
    """
    Parses a reassembled CCSDS TC transfer frame (TFPH, optional CSP/
    Encapsulation/SDLS headers, data, optional SDLS trailer, FECF) and
    republishes the parsed fields as PDU metadata alongside the
    extracted data payload.
    """
    def __init__(self, sdls_type:int=3, encapsulation_used:bool=True, data_type:int=0):
        """
        Args:
            sdls_type (int): 0=No SDLS, 1=Encryption only,
                2=Authentication only, 3=Both.
            encapsulation_used (bool): whether to expect an
                Encapsulation Packet Protocol header.
            data_type (int): 0=Raw, 1=CSP (adds a csp_header field).

        Raises:
            ValueError: sdls_type is outside 0-3, or data_type is
                outside 0-1.
        """
        gr.basic_block.__init__(self,
            name="CCSDS Reader",
            in_sig=None,
            out_sig=None)

        if sdls_type < 0 or sdls_type > 3:
            raise ValueError(f"Invalid sdls_type: {sdls_type}. Must be between 0 and 3.")

        if data_type < 0 or data_type > 1:
            raise ValueError(f"Invalid data_type: {data_type}. Must be between 0 and 1.")

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
        Encapsulation Packet Protocol Header (CCSDS 133.1-B), 1-8 bytes
        depending on its Length of Length field - see encapsulation_packet.
        """
        return encapsulation_packet.HEADER_STRUCT

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
            return encapsulation_packet.header_length(int(encap_header.get("length_of_length", 0)))
        return encapsulation_packet.header_length(int(encap_header.length_of_length))

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
        """
        Args:
            value: Python scalar/bytes/str/dict-like value to convert,
                as produced by parsing a construct.Struct/Container.

        Returns:
            pmt: PMT_NIL for None, otherwise the equivalent PMT value
                (bool/int/u8vector/symbol/dict), recursing into nested
                dict-likes.
        """
        if value is None:
            return pmt.PMT_NIL
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
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload,
                the reassembled CCSDS TC transfer frame to parse.

        Publishes:
            "debug" (pmt_pair): PDU extending the input metadata dict
                with parsed telecommand/sdls/encapsulation_header fields,
                payload the frame's extracted data field.

        Drops when:
            - msg is not a pair (warn - raw RF data, malformed before any structural check)
            - the payload is not a u8vector (warn - same)
            - parsing the frame or building its metadata fails for any reason (warn - same)
        """
        try:
            if not pmt.is_pair(msg):
                self.logger.warn("Received message is not a PDU (pair).")
                return

            in_meta = pmt.car(msg)
            in_body = pmt.cdr(msg)
            if not pmt.is_u8vector(in_body):
                self.logger.warn("Received message body is not a u8vector.")
                return

            frame_bytes = bytes(pmt.u8vector_elements(in_body))

            parsed = self.ccsds_message().parse(frame_bytes)

            total_length = int(parsed.tc_header.frame_length) + 1
            encap_len = self._encapsulation_header_length(getattr(parsed, "encapsulation_header", None))
            sdls_header_len = self._sdls_header_length() if self.sdls_type != 0 else 0
            sdls_trailer_len = 16 if self.sdls_type in (2, 3) else 0
            data_len = len(parsed.data) if hasattr(parsed, "data") and parsed.data is not None else 0
            overhead = 5 + 2 + (6 if self.data_type == 1 else 0) + encap_len + sdls_header_len + sdls_trailer_len
            self.logger.debug(
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

            self.logger.debug("OK")
            return msg
        except Exception as exc:
            self.logger.warn(f"Failed to parse or publish CCSDS frame: {exc}")
            return
