#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from construct import Bytes, Int16ub, Struct
from gnuradio import gr
import pmt

REQUIRED_IV_LENGTH_BYTES = 2

class sdls_header(gr.basic_block):
    """
    Build and prepend a CCSDS 355.0-B-1 SDLS Security Header (SPI + IV) to
    a PDU's payload.

    Reads `spi` and `sdls_counter` from the input PDU's metadata dict
    (checked at the top level first, falling back to the nested
    `sdls.security_header.<key>` path only if the top-level key is
    absent), builds a `spi (2 bytes) || sdls_counter (2 bytes)` header,
    and prepends it to the payload. Removes `spi`/`sdls_counter` from the
    outgoing metadata.
    """
    def __init__(self, iv_length_bytes: int = REQUIRED_IV_LENGTH_BYTES):
        """
        Args:
            iv_length_bytes (int): fixed byte width sdls_counter is
                padded/validated to when building the header. Must be 2:
                sdls_encryption/sdls_authentication's AES-CTR/CMAC
                counter block is always exactly 2 bytes wide (a 14-byte
                nonce leaves 2 bytes of a 16-byte AES block for
                sdls_encryption/sdls_decryption; sdls_authentication/
                sdls_authentication_verify hardcode
                counter.to_bytes(2, ...) directly) - any other value
                here would make the wire-transmitted sdls_counter field
                width disagree with what TX/RX actually used
                cryptographically.

        Raises:
            ValueError: iv_length_bytes is not 2.
        """
        gr.basic_block.__init__(self,
            name="SDLS Header",
            in_sig=None,
            out_sig=None)


        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        if iv_length_bytes != REQUIRED_IV_LENGTH_BYTES:
            raise ValueError(
                f"iv_length_bytes must be {REQUIRED_IV_LENGTH_BYTES} (the fixed counter width "
                f"sdls_encryption/sdls_authentication use), got {iv_length_bytes}."
            )
        self.iv_length_bytes = iv_length_bytes

        # Built once per instance, not per message: sdls_counter is
        # always padded/validated to exactly self.iv_length_bytes before
        # this runs, so the field width - and therefore this whole
        # schema - never varies across messages for a given instance.
        self._header_struct = Struct(
            "spi" / Int16ub,
            "sdls_counter" / Bytes(self.iv_length_bytes),
        )

        # Handler
        self.set_msg_handler(pmt.intern("in"), self.add_header)

    def _extract_dictionary_key(self, dict_msg, key_name: str, fixed_length_bytes: int | None = None) -> bytes | None:
        """
        Args:
            dict_msg (pmt_dict): dict to read key_name from.
            key_name (str): metadata key to extract.
            fixed_length_bytes (int | None): if given, the value is
                validated/padded to exactly this many bytes.

        Returns:
            bytes | None: the extracted value, or None if key_name is
                absent, malformed, an unsupported type, a negative
                integer, or (with fixed_length_bytes set) longer than
                fixed_length_bytes.
        """
        # Extract and validate bytes from dict
        value_pmt = pmt.dict_ref(dict_msg, pmt.intern(key_name), pmt.PMT_NIL)
        if pmt.eqv(value_pmt, pmt.PMT_NIL):
            self.logger.error(f"Received dict message with empty '{key_name}': {dict_msg}")
            return None

        # Parse the value as bytes.
        if pmt.is_symbol(value_pmt):
            key_str = pmt.symbol_to_string(value_pmt).strip()
            try:
                value = bytes.fromhex(key_str)
            except ValueError:
                self.logger.error(f"'{key_name}' symbol must be a valid hex string.")
                return None
        elif pmt.is_u8vector(value_pmt):
            value = bytes(pmt.u8vector_elements(value_pmt))
        elif pmt.is_integer(value_pmt) or pmt.is_uint64(value_pmt):
            if fixed_length_bytes is None or fixed_length_bytes <= 0:
                self.logger.error(
                    f"Received integer value for key '{key_name}' in metadata, but no fixed length specified: {dict_msg}"
                )
                return None

            int_value = int(pmt.to_uint64(value_pmt)) if pmt.is_uint64(value_pmt) else int(pmt.to_long(value_pmt))
            if int_value < 0:
                self.logger.error(f"Received negative integer for key '{key_name}' in metadata: {dict_msg}")
                return None

            try:
                value = int_value.to_bytes(fixed_length_bytes, 'big')
            except OverflowError:
                self.logger.error(
                    f"'{key_name}' integer value {int_value} exceeds fixed length {fixed_length_bytes} bytes."
                )
                return None
        else:
            self.logger.error(
                f"Received '{key_name}' of unsupported type "
                f"(expected symbol hex string, u8vector, or integer)."
            )
            return None

        if fixed_length_bytes is not None:
            if len(value) > fixed_length_bytes:
                self.logger.error(
                    f"'{key_name}' length {len(value)} exceeds fixed length {fixed_length_bytes} bytes."
                )
                return None
            elif len(value) < fixed_length_bytes:
                padding = fixed_length_bytes - len(value)
                self.logger.info(
                    f"'{key_name}' length {len(value)} is less than {fixed_length_bytes}; "
                    f"padding with {padding} zero bytes."
                )
                value = value + bytes(padding)

        return value

    def _extract_with_fallback(self, dict_msg, security_header, key_name: str, fixed_length_bytes: int) -> bytes | None:
        """
        Args:
            dict_msg (pmt_dict): top-level PDU metadata dict.
            security_header (pmt_dict | PMT_NIL): dict_msg["sdls"]["security_header"], or PMT_NIL if absent.
            key_name (str): metadata key to extract.
            fixed_length_bytes (int): fixed byte width to validate/pad to.

        Returns:
            bytes | None: checked at dict_msg[key_name] first, falling
                back to security_header[key_name] only if the top-level
                key is genuinely absent. None if neither is present or
                extraction fails.
        """
        if pmt.dict_has_key(dict_msg, pmt.intern(key_name)):
            return self._extract_dictionary_key(dict_msg, key_name, fixed_length_bytes=fixed_length_bytes)
        if not pmt.eqv(security_header, pmt.PMT_NIL) and pmt.is_dict(security_header):
            return self._extract_dictionary_key(security_header, key_name, fixed_length_bytes=fixed_length_bytes)
        self.logger.error(
            f"'{key_name}' not found in metadata (checked top-level and sdls.security_header): {dict_msg}"
        )
        return None


    def add_header(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (must include `spi`,
                directly or under `sdls.security_header`, and
                `sdls_counter` likewise) and u8vector payload.

        Publishes:
            "out" (pmt_pair): PDU with `spi`/`sdls_counter` removed from
                metadata and the payload prefixed by the
                `spi (2 bytes) || sdls_counter (iv_length_bytes)` header.

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - spi is absent, malformed, or too long for 2 bytes (error - same)
            - sdls_counter is absent, malformed, or too long for iv_length_bytes (error - same)
            - header packing or publishing fails (error - same)
        """
        # Log the received message
        self.logger.trace(f"Received message: {msg}")

        # Expect a GNU Radio PDU: (metadata . payload)
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
        # final publish - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
            sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
            security_header = pmt.PMT_NIL
            if not pmt.eqv(sdls, pmt.PMT_NIL) and pmt.is_dict(sdls):
                security_header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)

            # Extract and validate spi and sdls_counter from dict, each
            # checked at the top level first, falling back to
            # sdls.security_header only if genuinely absent.
            spi = self._extract_with_fallback(dict_msg, security_header, "spi", 2)
            if spi is None:
                return

            sdls_counter = self._extract_with_fallback(dict_msg, security_header, "sdls_counter", self.iv_length_bytes)
            if sdls_counter is None:
                return

            # SDLS header: SPI is 16 bits, counter length is byte-aligned and variable.
            header = self._header_struct.build(
                {
                    "spi": int.from_bytes(spi, byteorder="big"),
                    "sdls_counter": sdls_counter,
                }
            )

            # Remove the used keys from the dict to avoid confusion downstream.
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("spi"))
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("sdls_counter"))

            # Combine header and payload, then create a new PDU message with the modified payload.
            framed_payload = header + bytes(pmt.u8vector_elements(payload))
            pmt_payload = pmt.init_u8vector(len(framed_payload), list(framed_payload))
            pdu_msg_out = pmt.cons(dict_msg, pmt_payload)

            # Send the modified message to the output port
            self.message_port_pub(pmt.intern("out"), pdu_msg_out)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build header or publish message: {exc}")
            return
