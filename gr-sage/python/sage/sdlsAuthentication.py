#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import logging
import pmt
from gnuradio import gr

from Crypto.Hash import CMAC
from Crypto.Cipher import AES


COUNTER_MIN = 0
COUNTER_MAX = 0xFFFF  # 2-byte counter max value
NONCE_LEN = 14

class sdlsAuthentication(gr.basic_block):
    """
    docstring for block sdlsAuthentication
    """
    def __init__(self, state:bool=True, nonce:bytes=b"\x00" * 14):
        gr.basic_block.__init__(self,
            name="SDLS Authentication",
            in_sig=None,
            out_sig=None)
        
        self.logger = logging.getLogger("gnuradio.sage.sdlsAuthentication")

        self.state = state

        if not isinstance(nonce, (bytes, bytearray)):
            raise TypeError("nonce must be bytes-like")
        if len(nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")

        self.nonce = bytes(nonce)

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Handler
        self.set_msg_handler(pmt.intern("in"), self.add_authentication)


    def _extract_secret(self, dict_msg) -> bytes | None:

        # Extract and validate the authentication key from dict
        key = pmt.dict_ref(dict_msg, pmt.intern("auth_key"), pmt.PMT_NIL)
        if pmt.eqv(key, pmt.PMT_NIL):
            self.logger.warning(f"Received dict message with empty auth_key: {dict_msg}")
            return None # Early exit if auth_key is empty
        
        
        # Parse the key as bytes.
        if pmt.is_symbol(key):
            key_str = pmt.symbol_to_string(key).strip()
            try:
                secret = bytes.fromhex(key_str)
            except ValueError:
                self.logger.warning("auth_key symbol must be a valid hex string.")
                return None
        elif pmt.is_u8vector(key):
            secret = bytes(pmt.u8vector_elements(key))
        else:
            self.logger.warning("Received auth_key of unsupported type (expected symbol hex string or u8vector).")
            return None # Early exit if auth_key is of unsupported type
        
        if len(secret) != 32:
            self.logger.warning("auth_key length must be exactly 32 bytes (AES-256).")
            return None
        
        return secret
        
    def _extract_counter(self, dict_msg)->int|None:
         # Extract and validate the SDLS counter value from dict.
        counter = pmt.dict_ref(dict_msg, pmt.intern("sdls_counter"), pmt.PMT_NIL)

        if pmt.eqv(counter, pmt.PMT_NIL):
            self.logger.warning(f"Received dict message with empty sdls_counter: {dict_msg}")
            return None # Early exit if counter is empty

        if not (pmt.is_integer(counter) or pmt.is_uint64(counter)):
            self.logger.warning("sdls_counter must be an integer PMT value.")
            return None

        # Counter is an integer PMT
        if pmt.is_uint64(counter):
            counter = int(pmt.to_uint64(counter))
        else:
            counter = int(pmt.to_long(counter))

        # Validate counter range
        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            self.logger.error("sdls_counter exceeds 2-byte range (0..65535); aborting message processing.")
            return None
        
        return counter

    def _build_ctr_counter_block(self, counter:int) -> bytes:
        # CTR-style block: nonce (14 bytes) + sequence number (2 bytes).
        return self.nonce + counter.to_bytes(2, byteorder="big", signed=False)

    def _get_authentication_tag(self, secret:bytes, counter:int, payload_u8vector) -> bytes:

        # Convert payload from PMT u8vector to bytes
        payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))

        # Counter bytes: 14-byte nonce + 2-byte sequence number.
        counter_bytes = self._build_ctr_counter_block(counter)

        # Data to Authenticate: (CTR counter block || payload)
        data_to_authenticate = counter_bytes + payload_bytes

        # Compute CMAC tag
        cobj = CMAC.new(secret, ciphermod=AES)
        cobj.update(data_to_authenticate)
        mac_tag = cobj.digest()

        return mac_tag


    def add_authentication(self, msg):

        if self.state is False:
            self.logger.info("No authentication applied since state is False. Passing through message.")
            self.message_port_pub(pmt.intern("out"), msg)
            return # Early exit if authentication is disabled
        

        # Expecting a PDU with a dict containing 'auth_key' and 'sdls_counter', and a u8vector payload.

        if not pmt.is_pair(msg):
            self.logger.warning(f"Received non-PDU message: {msg}")
            return # Early exit if message is not a pair (dict, payload)

        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.warning(f"Received non-dict message: {dict_msg}")
            return # Early exit if message is not a dict
        
        if not pmt.is_u8vector(payload_u8vector):
            self.logger.warning(f"Received non-u8vector payload: {payload_u8vector}")
            return # Early exit if payload is not a u8vector
        

        # Extract the secret key from the message dict, with validation.
        secret_bytes = self._extract_secret(dict_msg)
        if secret_bytes is None:
            self.logger.warning(f"Failed to extract valid authentication key from message: {dict_msg}")
            return

        # Extract the counter value from the message dict, with validation.
        counter = self._extract_counter(dict_msg)
        if counter is None:
            self.logger.warning(f"Failed to extract valid sdls_counter from message: {dict_msg}")
            return
        
        # Compute the authentication tag (CMAC) over the nonce+counter and payload.
        mac_tag = self._get_authentication_tag(secret_bytes, counter, payload_u8vector)


        #  Append authentication tag.
        payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))
        out_payload = payload_bytes + mac_tag
        payload = pmt.init_u8vector(len(out_payload), list(out_payload))


        # delete the auth_key from the dict since they are not needed anymore
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("auth_key"))

        # sdls_counter is used for downstream SDLS processing, so we keep it in the dict.

        # emit the new PDU with the updated dict and payload
        new_msg = pmt.cons(dict_msg, payload)

        self.message_port_pub(pmt.intern("out"), new_msg)


