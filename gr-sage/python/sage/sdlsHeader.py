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

class sdlsHeader(gr.basic_block):
    """
    docstring for block sdlsHeader
    """
    def __init__(self, iv_length_bytes: int = 2):
        gr.basic_block.__init__(self,
            name="SDLS Header",
            in_sig=None,
            out_sig=None)
        

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        self.iv_length_bytes = iv_length_bytes

        # Handler
        self.set_msg_handler(pmt.intern("in"), self.add_header)

    def _extract_dictionary_key(self, dict_msg, key_name: str, fixed_length_bytes: int | None = None) -> bytes | None:

        # Extract and validate bytes from dict
        value_pmt = pmt.dict_ref(dict_msg, pmt.intern(key_name), pmt.PMT_NIL)
        if pmt.eqv(value_pmt, pmt.PMT_NIL):
            self.logger.warn(f"Received dict message with empty '{key_name}': {dict_msg}")
            return None

        # Parse the value as bytes.
        if pmt.is_symbol(value_pmt):
            key_str = pmt.symbol_to_string(value_pmt).strip()
            try:
                value = bytes.fromhex(key_str)
            except ValueError:
                self.logger.warn(f"'{key_name}' symbol must be a valid hex string.")
                return None
        elif pmt.is_u8vector(value_pmt):
            value = bytes(pmt.u8vector_elements(value_pmt))
        elif pmt.is_integer(value_pmt) or pmt.is_uint64(value_pmt):
            if fixed_length_bytes is None or fixed_length_bytes <= 0:
                self.logger.warn(
                    f"Received integer value for key '{key_name}' in metadata, but no fixed length specified: {dict_msg}"
                )
                return None

            int_value = int(pmt.to_uint64(value_pmt)) if pmt.is_uint64(value_pmt) else int(pmt.to_long(value_pmt))
            if int_value < 0:
                self.logger.warn(f"Received negative integer for key '{key_name}' in metadata: {dict_msg}")
                return None

            try:
                value = int_value.to_bytes(fixed_length_bytes, 'big')
            except OverflowError as exc:
                raise ValueError(
                    f"'{key_name}' integer value {int_value} exceeds fixed length {fixed_length_bytes} bytes."
                ) from exc
        else:
            self.logger.warn(
                f"Received '{key_name}' of unsupported type "
                f"(expected symbol hex string, u8vector, or integer)."
            )
            return None

        if fixed_length_bytes is not None:
            if len(value) > fixed_length_bytes:
                raise ValueError(
                    f"'{key_name}' length {len(value)} exceeds fixed length {fixed_length_bytes} bytes."
                )
            elif len(value) < fixed_length_bytes:
                padding = fixed_length_bytes - len(value)
                self.logger.info(
                    f"'{key_name}' length {len(value)} is less than {fixed_length_bytes}; "
                    f"padding with {padding} zero bytes."
                )
                value = value + bytes(padding)
        
        return value
        

    def add_header(self, msg):
        # Log the received message
        self.logger.debug(f"Received message: {msg}")

        # Expect a GNU Radio PDU: (metadata . payload)
        if not pmt.is_pair(msg):
            self.logger.warn(f"Received non-PDU message: {msg}")
            return
        
        dict_msg = pmt.car(msg)
        payload = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.warn(f"Received PDU with non-dict metadata: {dict_msg}")
            return

        if not pmt.is_u8vector(payload):
            self.logger.warn(f"Received PDU with non-u8vector payload: {payload}")
            return
        
        # Extract and validate spi from dict
        try:
            spi = self._extract_dictionary_key(dict_msg, "spi", fixed_length_bytes=2)
            if spi is None:
                self.logger.warn(f"Failed to extract spi from message: {msg}")
                return

            # Extract and validate sdls_counter from dict
            sdls_counter = self._extract_dictionary_key(dict_msg, "sdls_counter", fixed_length_bytes=self.iv_length_bytes)
            if sdls_counter is None:
                self.logger.warn(f"Failed to extract sdls_counter from message: {msg}")
                return
        except ValueError as exc:
            self.logger.error(str(exc))
            return

        # SDLS header: SPI is 16 bits, counter length is byte-aligned and variable.
        header_struct = Struct(
            "spi" / Int16ub,
            "sdls_counter" / Bytes(len(sdls_counter)),
        )
        header = header_struct.build(
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
        



