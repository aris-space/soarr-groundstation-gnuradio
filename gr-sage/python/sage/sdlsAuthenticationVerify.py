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

from sage.sdlsAuthentication import COUNTER_MAX, COUNTER_MIN, NONCE_LEN


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
        split = self._split_payload_tag(payload_bytes)
        if split is None:
            return
        payload, tag = split

        if not self._verify_tag(secret_bytes, counter, payload, tag):
            self.logger.warn("Authentication tag verification failed; dropping message.")
            return

        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("auth_key"))
        out_payload = pmt.init_u8vector(len(payload), list(payload))
        msg_out = pmt.cons(dict_msg, out_payload)
        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info("OK")
        