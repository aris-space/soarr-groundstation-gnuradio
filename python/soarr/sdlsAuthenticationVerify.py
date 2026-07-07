#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from Crypto.Hash import CMAC
from Crypto.Cipher import AES

from soarr.sdlsAuthentication import COUNTER_MAX, COUNTER_MIN, NONCE_LEN


TAG_LEN = 16

class sdlsAuthenticationVerify(gr.basic_block):
    """
    docstring for block sdlsAuthenticationVerify
    """
    def __init__(self, authentication_state:bool=True, nonce: bytes = b"\x00" * NONCE_LEN):
        gr.basic_block.__init__(self,
            name="sdlsAuthenticationVerify",
            in_sig=None,
            out_sig=None)

        self.authentication_state = authentication_state
        if not isinstance(nonce, (bytes, bytearray)):
            raise TypeError("nonce must be bytes-like")
        if len(nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")
        self.nonce = bytes(nonce)

        # Register message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Set the message handler for the input port
        self.set_msg_handler(pmt.intern("in"), self.verify_message)

    def _extract_secret(self, dict_msg) -> bytes | None:
        key = pmt.dict_ref(dict_msg, pmt.intern("auth_key"), pmt.PMT_NIL)
        if pmt.eqv(key, pmt.PMT_NIL):
            self.logger.warn(f"Received dict message with empty auth_key: {dict_msg}")
            return None

        if pmt.is_symbol(key):
            key_str = pmt.symbol_to_string(key).strip()
            try:
                secret = bytes.fromhex(key_str)
            except ValueError:
                self.logger.warn(f"auth_key symbol must be a valid hex string: {key_str}")
                return None
        elif pmt.is_u8vector(key):
            secret = bytes(pmt.u8vector_elements(key))
        else:
            self.logger.warn(f"Received auth_key of unsupported type (expected symbol hex string or u8vector): {key}")
            return None

        if len(secret) != 32:
            self.logger.warn(f"auth_key length must be exactly 32 bytes (AES-256): {len(secret)}")
            return None

        return secret

    def _extract_counter(self, dict_msg) -> int | None:
        counter = pmt.dict_ref(dict_msg, pmt.intern("sdls_counter"), pmt.PMT_NIL)

        if pmt.eqv(counter, pmt.PMT_NIL):
            sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
            if not pmt.eqv(sdls, pmt.PMT_NIL) and pmt.is_dict(sdls):
                security_header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)
                if not pmt.eqv(security_header, pmt.PMT_NIL) and pmt.is_dict(security_header):
                    counter = pmt.dict_ref(security_header, pmt.intern("sdls_counter"), pmt.PMT_NIL)

        if pmt.eqv(counter, pmt.PMT_NIL):
            self.logger.warn(f"Received dict message with empty sdls_counter: {dict_msg}")
            return None

        if not (pmt.is_integer(counter) or pmt.is_uint64(counter)):
            self.logger.warn(f"sdls_counter must be an integer PMT value: {counter}")
            return None

        if pmt.is_uint64(counter):
            counter = int(pmt.to_uint64(counter))
        else:
            counter = int(pmt.to_long(counter))

        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            self.logger.error(
                f"sdls_counter exceeds 2-byte range (0..65535); aborting message processing. Value: {counter}"
            )
            return None

        return counter

    def _build_ctr_counter_block(self, counter: int) -> bytes:
        return self.nonce + counter.to_bytes(2, byteorder="big", signed=False)

    def _split_payload_tag(self, payload_bytes: bytes) -> tuple[bytes, bytes] | None:
        if len(payload_bytes) < TAG_LEN:
            self.logger.warn("Payload too short to contain authentication tag.")
            return None

        return payload_bytes[:-TAG_LEN], payload_bytes[-TAG_LEN:]

    def _pmt_dict_get_int(self, meta, key, default=None):
        if not pmt.is_dict(meta):
            return default
        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            return default
        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return default
        try:
            return int(pmt.to_long(value))
        except Exception:
            try:
                return int(pmt.to_uint64(value))
            except Exception:
                return default

    def _build_encapsulation_header(self, encap_meta) -> bytes | None:
        if not pmt.is_dict(encap_meta):
            return None

        length_of_length = self._pmt_dict_get_int(encap_meta, "length_of_length", None)
        if length_of_length is None:
            return None

        first_octet = self._pmt_dict_get_int(encap_meta, "first_octet", None)
        if first_octet is None:
            packet_version = self._pmt_dict_get_int(encap_meta, "packet_version", 0)
            protocol_id = self._pmt_dict_get_int(encap_meta, "protocol_id", 0)
            first_octet = ((packet_version & 0x7) << 5) | ((protocol_id & 0x7) << 2) | (length_of_length & 0x3)

        header = bytearray([first_octet & 0xFF])

        if length_of_length >= 0b10:
            user_defined_field = self._pmt_dict_get_int(encap_meta, "user_defined_field", 0)
            protocol_id_extension = self._pmt_dict_get_int(encap_meta, "protocol_id_extension", 0)
            header.append(((user_defined_field & 0xF) << 4) | (protocol_id_extension & 0xF))

        if length_of_length >= 0b11:
            ccsds_defined_field = self._pmt_dict_get_int(encap_meta, "ccsds_defined_field", 0)
            header.extend(int(ccsds_defined_field).to_bytes(2, byteorder="big", signed=False))

        if length_of_length != 0b00:
            packet_length = self._pmt_dict_get_int(encap_meta, "packet_length", None)
            if packet_length is None:
                return None
            length_bytes = {0b01: 1, 0b10: 2, 0b11: 4}.get(length_of_length, 0)
            header.extend(int(packet_length).to_bytes(length_bytes, byteorder="big", signed=False))

        return bytes(header)

    def _verify_tag(self, secret: bytes, counter: int, payload_bytes: bytes, tag: bytes) -> bool:
        counter_bytes = self._build_ctr_counter_block(counter)
        verifier = CMAC.new(secret, ciphermod=AES)
        verifier.update(counter_bytes + payload_bytes)
        try:
            verifier.verify(tag)
        except ValueError:
            return False
        return True

    def verify_message(self, msg):
        # Validate the input message format
        if not pmt.is_pair(msg):
            raise ValueError("Expected a PMT pair for decryption input")
        
        # Extract the dict and payload from the PDU
        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_u8vector(payload_u8vector):
            raise ValueError("Expected the payload to be a PMT u8vector")
        
        if not pmt.is_dict(dict_msg):
            raise ValueError("Expected the metadata to be a PMT dict")
        
        # If authentication state is False, pass through the message unmodified
        if not self.authentication_state:
            self.message_port_pub(pmt.intern("out"), msg)
            return
        
        # If authentication state is True, perform verification
        secret_bytes = self._extract_secret(dict_msg)
        if secret_bytes is None:
            self.logger.warn(f"Failed to extract valid authentication key from message: {dict_msg}")
            return

        counter = self._extract_counter(dict_msg)
        if counter is None:
            self.logger.warn(f"Failed to extract valid sdls_counter from message: {dict_msg}")
            return

        payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))

        tag = None
        encap_prefix = b""
        sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
        if not pmt.eqv(sdls, pmt.PMT_NIL) and pmt.is_dict(sdls):
            trailer = pmt.dict_ref(sdls, pmt.intern("security_trailer"), pmt.PMT_NIL)
            if pmt.is_u8vector(trailer):
                tag = bytes(pmt.u8vector_elements(trailer))

        encap_meta = pmt.dict_ref(dict_msg, pmt.intern("encapsulation_header"), pmt.PMT_NIL)
        if not pmt.eqv(encap_meta, pmt.PMT_NIL):
            encap_bytes = self._build_encapsulation_header(encap_meta)
            if encap_bytes is not None:
                encap_prefix = encap_bytes

        if tag is None:
            split = self._split_payload_tag(payload_bytes)
            if split is None:
                return
            payload, tag = split
            mac_payload = payload
            out_payload_bytes = payload
        else:
            has_encap = bool(encap_prefix) and payload_bytes.startswith(encap_prefix)
            mac_payload = payload_bytes if has_encap else encap_prefix + payload_bytes
            out_payload_bytes = payload_bytes[len(encap_prefix):] if has_encap else payload_bytes

        if not self._verify_tag(secret_bytes, counter, mac_payload, tag):
            self.logger.warn("Authentication tag verification failed; dropping message.")
            return

        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("auth_key"))
        out_payload = pmt.init_u8vector(len(out_payload_bytes), list(out_payload_bytes))
        msg_out = pmt.cons(dict_msg, out_payload)
        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info("OK")
        