#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from Crypto.Cipher import AES


COUNTER_MIN = 0
COUNTER_MAX = 0xFFFF  # 2-byte counter max value
COUNTER_SIZE = 2  # 2 bytes for the counter
NONCE_LEN = 14

class sdlsDecryption(gr.basic_block):
    """
    docstring for block sdlsDecryption
    """
    def __init__(self, decryption_state: bool = True, nonce: bytes = b"\x00" * NONCE_LEN):
        gr.basic_block.__init__(self,
            name="sdlsDecryption",
            in_sig=None,
            out_sig=None)

        self.decryption_state = decryption_state
        if not isinstance(nonce, (bytes, bytearray)):
            raise TypeError("nonce must be bytes-like")
        if len(nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")
        self.nonce = bytes(nonce)

        # Register message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Set the message handler for the input port
        self.set_msg_handler(pmt.intern("in"), self.decrypt_message)


    def _validate_and_extract_key(self, dict_msg) -> bytes | None:
        # Extract and validate the decryption key from dict
        key = pmt.dict_ref(dict_msg, pmt.intern("crypt_key"), pmt.PMT_NIL)
        if pmt.eqv(key, pmt.PMT_NIL):
            self.logger.warn(f"Received dict message with empty crypt_key: {dict_msg}")
            return None # Early exit if crypt_key is empty

        
        # Parse the key as bytes.
        if pmt.is_symbol(key):
            key_str = pmt.symbol_to_string(key).strip()
            try:
                secret = bytes.fromhex(key_str)
            except ValueError:
                self.logger.warn(f"crypt_key symbol must be a valid hex string: {key_str}")
                return None
        elif pmt.is_u8vector(key):
            secret = bytes(pmt.u8vector_elements(key))
        else:
            self.logger.warn(f"Received crypt_key of unsupported type (expected symbol hex string or u8vector): {key}")
            return None

        if len(secret) != 32:
            self.logger.warn(f"crypt_key length must be exactly 32 bytes (AES-256); got {len(secret)}")
            return None

        return secret

    def _extract_counter(self, dict_msg) -> int | None:
        # Extract and validate the SDLS counter value from dict.
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

    def _decrypt_payload(self, key: bytes, counter: int, payload: bytes) -> bytes:
        if len(self.nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")

        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            raise ValueError("sdls_counter must be in range 0..65535")

        cipher = AES.new(key, AES.MODE_CTR, nonce=self.nonce, initial_value=counter)
        plaintext = cipher.decrypt(payload)

        return plaintext


    def decrypt_message(self, msg):

        if not pmt.is_pair(msg):
            raise ValueError("Expected a PMT pair for decryption input")
        
        # Extract the dict and payload from the PDU
        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_u8vector(payload_u8vector):
            raise ValueError("Expected the payload to be a PMT u8vector")
        
        if not pmt.is_dict(dict_msg):
            raise ValueError("Expected the metadata to be a PMT dict")
        

        if self.decryption_state is False:
            # If decryption is disabled, pass the message through unchanged
            self.message_port_pub(pmt.intern("out"), msg)
            return
        
        # Extract the decryption key from the dict message
        key = self._validate_and_extract_key(dict_msg)
        if key is None:
            self.logger.warn("Failed to extract valid decryption key from message dict; aborting decryption.")
            return

        counter = self._extract_counter(dict_msg)
        if counter is None:
            self.logger.warn("Failed to extract valid counter from message dict; aborting decryption.")
            return

        payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))
        plaintext_bytes = self._decrypt_payload(key, counter, payload_bytes)

        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("crypt_key"))

        plaintext_u8vector = pmt.init_u8vector(len(plaintext_bytes), list(plaintext_bytes))
        msg_out = pmt.cons(dict_msg, plaintext_u8vector)

        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info("OK")
        

        

        


