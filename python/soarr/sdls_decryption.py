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

class sdls_decryption(gr.basic_block):
    """
    Decrypt a PDU's payload with AES-256-CTR (CCSDS 355.0-B-1 SDLS).

    Reads the key (`crypt_key`) and per-message counter (`sdls_counter`)
    from the input PDU's metadata dict, decrypts the payload, and removes
    `crypt_key` from the outgoing metadata. `sdls_counter` and every other
    metadata key pass through unchanged.
    """
    def __init__(self, decryption_state: bool = True, nonce: bytes = b"\x00" * NONCE_LEN):
        """
        Args:
            decryption_state (bool): if False, decrypt_message becomes a
                pure passthrough after its shape checks - no validation,
                no metadata mutation.
            nonce (bytes): fixed 14-byte value combined with the
                per-message sdls_counter into the same 16-byte AES-CTR
                counter block sdls_encryption used to encrypt. Must match
                sdls_encryption's own nonce out-of-band.

        Raises:
            TypeError: nonce is not bytes-like.
            ValueError: nonce is not exactly 14 bytes.
        """
        gr.basic_block.__init__(self,
            name="sdls_decryption",
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
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            bytes | None: 32-byte AES-256 key, or None if `crypt_key` is
                absent, malformed, or the wrong length.
        """
        # Extract and validate the decryption key from dict
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
            return None

        if len(secret) != 32:
            self.logger.error(f"crypt_key length must be exactly 32 bytes (AES-256); got {len(secret)}")
            return None

        return secret

    def _extract_counter(self, dict_msg) -> int | None:
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            int | None: sdls_counter in range 0-65535, checked first at
                dict_msg["sdls_counter"], falling back to
                dict_msg["sdls"]["security_header"]["sdls_counter"] only
                if the top-level key is genuinely absent. None if neither
                is present, the value isn't an integer PMT, or it's out
                of range.
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
            return None

        if not (pmt.is_integer(counter) or pmt.is_uint64(counter)):
            self.logger.error(f"sdls_counter must be an integer PMT value: {counter}")
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
        """
        Args:
            key (bytes): 32-byte AES-256 key.
            counter (int): sdls_counter, 0-65535.
            payload (bytes): ciphertext to decrypt.

        Returns:
            bytes: AES-256-CTR plaintext, same length as payload.

        Raises:
            ValueError: self.nonce or counter is out of range (defensive
                - both are already validated before this is called).
        """
        if len(self.nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")

        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            raise ValueError("sdls_counter must be in range 0..65535")

        cipher = AES.new(key, AES.MODE_CTR, nonce=self.nonce, initial_value=counter)
        plaintext = cipher.decrypt(payload)

        return plaintext


    def decrypt_message(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (must include
                `crypt_key` and `sdls_counter` unless decryption_state is
                False) and u8vector payload (ciphertext).

        Publishes:
            "out" (pmt_pair): PDU with `crypt_key` removed from metadata
                and the payload replaced by its AES-256-CTR plaintext. If
                decryption_state is False, republishes the input
                unchanged instead (after the same shape checks).

        Drops when:
            - msg is not a PDU pair (error - malformed input, this block sits downstream of ccsds_reader)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - crypt_key is absent, malformed, or not 32 bytes (error - same)
            - sdls_counter is absent, non-integer, or out of range (error - same)
            - decryption or publishing fails, in either the decryption or the decryption_state=False passthrough path (error - same)
        """
        # Full body wrapped in catch-log-drop, including the final
        # publish - a raise anywhere in here must never escape this
        # handler.
        try:
            if not pmt.is_pair(msg):
                self.logger.error(f"Received non-PDU message: {msg}")
                return

            dict_msg = pmt.car(msg)
            payload_u8vector = pmt.cdr(msg)

            if not pmt.is_u8vector(payload_u8vector):
                self.logger.error(f"Received non-u8vector payload: {payload_u8vector}")
                return

            if not pmt.is_dict(dict_msg):
                self.logger.error(f"Received non-dict metadata: {dict_msg}")
                return

            if self.decryption_state is False:
                # If decryption is disabled, pass the message through unchanged
                self.message_port_pub(pmt.intern("out"), msg)
                return

            # Extract the decryption key from the dict message
            key = self._validate_and_extract_key(dict_msg)
            if key is None:
                self.logger.error("Failed to extract valid decryption key from message dict; aborting decryption.")
                return

            counter = self._extract_counter(dict_msg)
            if counter is None:
                self.logger.error("Failed to extract valid counter from message dict; aborting decryption.")
                return

            payload_bytes = bytes(pmt.u8vector_elements(payload_u8vector))
            plaintext_bytes = self._decrypt_payload(key, counter, payload_bytes)

            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("crypt_key"))

            plaintext_u8vector = pmt.init_u8vector(len(plaintext_bytes), list(plaintext_bytes))
            msg_out = pmt.cons(dict_msg, plaintext_u8vector)

            self.message_port_pub(pmt.intern("out"), msg_out)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to decrypt or publish message: {exc}")
            return
