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
NONCE_LEN = 14

class sdls_encryption(gr.basic_block):
    """
    Encrypt a PDU's payload with AES-256-CTR (CCSDS 355.0-B-1 SDLS).

    Reads the key (`crypt_key`) and per-message counter (`sdls_counter`)
    from the input PDU's metadata dict, encrypts the payload, and removes
    `crypt_key` from the outgoing metadata. `sdls_counter` and every other
    metadata key pass through unchanged - `sdls_header`, downstream, needs
    `sdls_counter` to embed it on the wire as the SDLS Security Header's
    IV field.
    """
    def __init__(self, encryption_state:bool=True, nonce:bytes=b"\x00" * NONCE_LEN):
        """
        Args:
            encryption_state (bool): if False, add_encryption becomes a
                pure passthrough after its shape checks - no validation,
                no metadata mutation.
            nonce (bytes): fixed 14-byte value combined with the
                per-message sdls_counter into one 16-byte AES-CTR counter
                block. Never transmitted - RX's sdls_decryption must be
                configured with the identical value out-of-band.

        Raises:
            TypeError: nonce is not bytes-like.
            ValueError: nonce is not exactly 14 bytes.
        """
        gr.basic_block.__init__(self,
            name="SDLS Encryption",
            in_sig=None,
            out_sig=None)


        self.encryption_state = encryption_state
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
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            bytes | None: 32-byte AES-256 key, or None if `crypt_key` is
                absent, malformed, or the wrong length.
        """
        # Extract and validate the encryption key from dict
        if not pmt.dict_has_key(dict_msg, pmt.intern("crypt_key")):
            self.logger.error(f"Received dict message with missing crypt_key: {dict_msg}")
            return None # Early exit if crypt_key is absent

        key = pmt.dict_ref(dict_msg, pmt.intern("crypt_key"), pmt.PMT_NIL)

        # Parse the key as bytes.
        if pmt.is_symbol(key):
            key_str = pmt.symbol_to_string(key).strip()
            try:
                secret = bytes.fromhex(key_str)
            except ValueError:
                self.logger.error(f"crypt_key symbol must be a valid hex string: {key_str}")
                return None
        elif pmt.is_u8vector(key):
            secret = bytes(pmt.u8vector_elements(key))
        else:
            self.logger.error(f"Received crypt_key of unsupported type (expected symbol hex string or u8vector): {key}")
            return None # Early exit if crypt_key is of unsupported type

        if len(secret) != 32:
            self.logger.error(f"crypt_key length must be exactly 32 bytes (AES-256); got {len(secret)}")
            return None

        return secret

    def _extract_counter(self, dict_msg)->int|None:
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            int | None: sdls_counter in range 0-65535, checked first at
                dict_msg["sdls_counter"], falling back to
                dict_msg["sdls"]["security_header"]["sdls_counter"] only
                if the top-level key is absent. None if neither is
                present, the value isn't an integer PMT, or it's out of
                range.
        """
        # Extract and validate the SDLS counter value from dict.
        if pmt.dict_has_key(dict_msg, pmt.intern("sdls_counter")):
            counter = pmt.dict_ref(dict_msg, pmt.intern("sdls_counter"), pmt.PMT_NIL)
        else:
            counter = pmt.PMT_NIL
            sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
            if not pmt.eqv(sdls, pmt.PMT_NIL) and pmt.is_dict(sdls):
                security_header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)
                if not pmt.eqv(security_header, pmt.PMT_NIL) and pmt.is_dict(security_header):
                    counter = pmt.dict_ref(security_header, pmt.intern("sdls_counter"), pmt.PMT_NIL)

        if pmt.eqv(counter, pmt.PMT_NIL):
            self.logger.error(f"Received dict message with empty sdls_counter: {dict_msg}")
            return None # Early exit if counter is empty

        if not (pmt.is_integer(counter) or pmt.is_uint64(counter)):
            self.logger.error(f"sdls_counter must be an integer PMT value: {counter}")
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
        """
        Args:
            key (bytes): 32-byte AES-256 key.
            counter (int): sdls_counter, 0-65535.
            payload (bytes): plaintext to encrypt.

        Returns:
            bytes: AES-256-CTR ciphertext, same length as payload.

        Raises:
            ValueError: self.nonce or counter is out of range (defensive
                - both are already validated before this is called).
        """
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
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (must include
                `crypt_key` and `sdls_counter`) and u8vector payload.

        Publishes:
            "out" (pmt_pair): PDU with `crypt_key` removed from metadata
                and the payload replaced by its AES-256-CTR ciphertext.
                If encryption_state is False, republishes the input
                unchanged instead (after the same shape checks).

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - payload is not a u8vector (error - same)
            - metadata is not a dict (error - same)
            - crypt_key is absent, malformed, or not 32 bytes (error - same)
            - sdls_counter is absent, non-integer, or out of range (error - same)
            - encryption, passthrough, or publishing fails, in either the encrypted or the encryption_state=False passthrough path (error - same)
        """
        # Expecting a PDU with dict and payload

        # Full body wrapped in catch-log-drop, including the final
        # publish - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
            if not pmt.is_pair(msg):
                self.logger.error(f"Received non-PDU message: {msg}")
                return

            # Extract the dict and payload from the PDU
            dict_msg = pmt.car(msg)
            payload_u8vector = pmt.cdr(msg)

            if not pmt.is_u8vector(payload_u8vector):
                self.logger.error(f"Received message with non-u8vector payload: {msg}")
                return

            if not pmt.is_dict(dict_msg):
                self.logger.error(f"Received message with non-dict metadata: {msg}")
                return

            if self.encryption_state is False:
                self.logger.info(f"No encryption applied since encryption_state is False. Passing through message.")
                self.message_port_pub(pmt.intern("out"), msg)
                return # Early exit if encryption is disabled

            # Extract the encryption key from the dict
            key_bytes = self._extract_secret(dict_msg)
            if key_bytes is None:
                return

            # Extract the counter value from the dict
            counter = self._extract_counter(dict_msg)
            if counter is None:
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
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to encrypt, pass through, or publish message: {exc}")
            return
