#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import pmt
from gnuradio import gr

from Crypto.Hash import CMAC
from Crypto.Cipher import AES


COUNTER_MIN = 0
COUNTER_MAX = 0xFFFF  # 2-byte counter max value
NONCE_LEN = 14

class sdls_authentication(gr.basic_block):
    """
    Compute and append a CMAC authentication tag to a PDU's payload
    (CCSDS 355.0-B-1 SDLS).

    Reads the key (`auth_key`) and per-message counter (`sdls_counter`)
    from the input PDU's metadata dict, computes an AES-CMAC tag over
    (nonce || counter || payload), and appends the 16-byte tag to the
    payload. Removes `auth_key` from the outgoing metadata; `sdls_counter`
    and every other metadata key pass through unchanged.
    """
    def __init__(self, authentication_state:bool=True, nonce:bytes=b"\x00" * NONCE_LEN):
        """
        Args:
            authentication_state (bool): if False, add_authentication
                becomes a pure passthrough - no validation, no metadata
                mutation.
            nonce (bytes): fixed 14-byte value combined with the
                per-message sdls_counter into the 16-byte block prepended
                to the payload before computing the CMAC tag. Never
                transmitted - RX's sdls_authentication_verify must be
                configured with the identical value out-of-band.
                Independent of sdls_encryption's own nonce parameter.

        Raises:
            TypeError: nonce is not bytes-like.
            ValueError: nonce is not exactly 14 bytes.
        """
        gr.basic_block.__init__(self,
            name="SDLS Authentication",
            in_sig=None,
            out_sig=None)

        self.authentication_state = authentication_state

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
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            bytes | None: 32-byte AES-256 key, or None if `auth_key` is
                absent, malformed, or the wrong length.
        """
        # Extract and validate the authentication key from dict
        key = pmt.dict_ref(dict_msg, pmt.intern("auth_key"), pmt.PMT_NIL)
        if pmt.eqv(key, pmt.PMT_NIL):
            self.logger.error(f"Received dict message with empty auth_key: {dict_msg}")
            return None # Early exit if auth_key is empty


        # Parse the key as bytes.
        if pmt.is_symbol(key):
            key_str = pmt.symbol_to_string(key).strip()
            try:
                secret = bytes.fromhex(key_str)
            except ValueError:
                self.logger.error(f"auth_key symbol must be a valid hex string: {key_str}")
                return None
        elif pmt.is_u8vector(key):
            secret = bytes(pmt.u8vector_elements(key))
        else:
            self.logger.error(f"Received auth_key of unsupported type (expected symbol hex string or u8vector): {key}")
            return None # Early exit if auth_key is of unsupported type

        if len(secret) != 32:
            self.logger.error(f"auth_key length must be exactly 32 bytes (AES-256): {len(secret)}")
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

    def _build_ctr_counter_block(self, counter:int) -> bytes:
        """
        Args:
            counter (int): sdls_counter, 0-65535.

        Returns:
            bytes: 16-byte block, self.nonce (14 bytes) || counter as a
                big-endian 2-byte sequence number.
        """
        # CTR-style block: nonce (14 bytes) + sequence number (2 bytes).
        return self.nonce + counter.to_bytes(2, byteorder="big", signed=False)

    def _get_authentication_tag(self, secret:bytes, counter:int, payload_u8vector) -> bytes:
        """
        Args:
            secret (bytes): 32-byte AES-256 key.
            counter (int): sdls_counter, 0-65535.
            payload_u8vector (pmt_u8vector): payload to authenticate.

        Returns:
            bytes: 16-byte AES-CMAC tag over (nonce || counter || payload).

        Raises:
            ValueError: self.nonce or counter is out of range (defensive
                - both are already validated before this is called).
        """
        # Defensive re-check, mirroring sdls_encryption._encrypt_payload -
        # both are already validated by the caller before this runs.
        if len(self.nonce) != NONCE_LEN:
            raise ValueError("nonce must be exactly 14 bytes")
        if counter < COUNTER_MIN or counter > COUNTER_MAX:
            raise ValueError("sdls_counter must be in range 0..65535")

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
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (must include
                `auth_key` and `sdls_counter`) and u8vector payload.

        Publishes:
            "out" (pmt_pair): PDU with `auth_key` removed from metadata
                and a 16-byte AES-CMAC tag appended to the payload. If
                authentication_state is False, republishes the input
                unchanged instead.

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - auth_key is absent, malformed, or not 32 bytes (error - same)
            - sdls_counter is absent, non-integer, or out of range (error - same)
            - tag computation or publishing fails, in either the tagging or the authentication_state=False passthrough path (error - same)
        """
        if self.authentication_state is False:
            try:
                self.logger.info(f"No authentication applied since authentication_state is False. Passing through message.")
                self.message_port_pub(pmt.intern("out"), msg)
            except Exception as exc:
                self.logger.error(f"Failed to publish passthrough message: {exc}")
            return # Early exit if authentication is disabled


        # Expecting a PDU with a dict containing 'auth_key' and 'sdls_counter', and a u8vector payload.

        if not pmt.is_pair(msg):
            self.logger.error(f"Received non-PDU message: {msg}")
            return # Early exit if message is not a pair (dict, payload)

        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.error(f"Received non-dict message: {dict_msg}")
            return # Early exit if message is not a dict

        if not pmt.is_u8vector(payload_u8vector):
            self.logger.error(f"Received non-u8vector payload: {payload_u8vector}")
            return # Early exit if payload is not a u8vector


        # Extract the secret key from the message dict, with validation.
        secret_bytes = self._extract_secret(dict_msg)
        if secret_bytes is None:
            return

        # Extract the counter value from the message dict, with validation.
        counter = self._extract_counter(dict_msg)
        if counter is None:
            return

        # Full body from here on wrapped in catch-log-drop, including the
        # final publish - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
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
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to compute tag or publish message: {exc}")
            return
