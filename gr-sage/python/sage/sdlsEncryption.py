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

class sdlsEncryption(gr.basic_block):
    """
    docstring for block sdlsEncryption
    """
    def __init__(self, state:bool=True, nonce:bytes=b"\x00" * NONCE_LEN):
        gr.basic_block.__init__(self,
            name="SDLS Encryption",
            in_sig=None,
            out_sig=None)
        
        
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
        self.set_msg_handler(pmt.intern("in"), self.add_encryption)


    def _extract_secret(self, dict_msg) -> bytes | None:

        # Extract and validate the encryption key from dict
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
            return None # Early exit if crypt_key is of unsupported type
        
        if len(secret) != 32:
            self.logger.warn(f"crypt_key length must be exactly 32 bytes (AES-256); got {len(secret)}")
            return None
        
        return secret
        
    def _extract_counter(self, dict_msg)->int|None:
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
            return None # Early exit if counter is empty

        if not (pmt.is_integer(counter) or pmt.is_uint64(counter)):
            self.logger.warn(f"sdls_counter must be an integer PMT value: {counter}")
            return None

        # Counter is an integer PMT
        if pmt.is_uint64(counter):
            counter = int(pmt.to_uint64(counter))
        else:
            counter = int(pmt.to_long(counter))

        # Validate counter range
        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            self.logger.error(f"sdls_counter exceeds 2-byte range (0..65535); aborting message processing. Value: {counter}")
            return None
        
        return counter
    
    def _encrypt_payload(self, key: bytes, counter: int, payload: bytes) -> bytes:
        # AES-CTR state is nonce (14 bytes) + 2-byte initial counter value.
        if len(self.nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")
        
        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            raise ValueError("sdls_counter must be in range 0..65535")
        
        # Encrypt the payload using AES-256 in CTR mode
        cipher = AES.new(key, AES.MODE_CTR, nonce=self.nonce, initial_value=counter)
        ciphertext = cipher.encrypt(payload)
        
        return ciphertext

    def add_encryption(self, msg):

        if self.state is False:
            self.logger.info(f"No encryption applied since state is False. Passing through message.")
            self.message_port_pub(pmt.intern("out"), msg)
            return # Early exit if encryption is disabled
        

        # Expecting a PDU with dict and payload
        if not pmt.is_pair(msg):
            self.logger.warn(f"Received non-PDU message: {msg}")
            return
        
        # Extract the dict and payload from the PDU
        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_u8vector(payload_u8vector):
            self.logger.warn(f"Received message with non-u8vector payload: {msg}")
            return
        
        if not pmt.is_dict(dict_msg):
            self.logger.warn(f"Received message with non-dict metadata: {msg}")
            return


        # Extract the encryption key from the dict
        key_bytes = self._extract_secret(dict_msg)
        if key_bytes is None:
            self.logger.warn(f"Failed to extract valid encryption key from message dict; aborting encryption.")
            return

        # Extract the counter value from the dict
        counter = self._extract_counter(dict_msg)
        if counter is None:
            self.logger.warn(f"Failed to extract valid counter from message dict; aborting encryption.")
            return
        

        # Encrypt the payload
        payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))
        ciphertext_bytes = self._encrypt_payload(key_bytes, counter, payload_bytes)

        # Remove the crypt_key from the dict since it's not needed anymore
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("crypt_key"))

        # Create a new u8vector for the ciphertext
        ciphertext_u8vector = pmt.init_u8vector(len(ciphertext_bytes), list(ciphertext_bytes))
        msg_out = pmt.cons(dict_msg, ciphertext_u8vector)

        # Emit the new PDU with the updated dict and encrypted payload
        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info(f"OK")








