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

from .sdls_authentication import COUNTER_MAX, COUNTER_MIN, NONCE_LEN


TAG_LEN = 16

class sdls_authentication_verify(gr.basic_block):
    """
    Verify and strip the CMAC authentication tag sdls_authentication
    appended on TX (CCSDS 355.0-B-1 SDLS).

    Reads the key (`auth_key`) and per-message counter (`sdls_counter`)
    from the input PDU's metadata dict, recomputes the AES-CMAC tag over
    (nonce || counter || payload), and only republishes the payload if it
    matches the tag supplied with the message. Removes `auth_key` from
    the outgoing metadata.
    """
    def __init__(self, authentication_state:bool=True, nonce: bytes = b"\x00" * NONCE_LEN):
        """
        Args:
            authentication_state (bool): if False, verify_message becomes
                a pure passthrough after its shape checks - no key/counter
                extraction, no tag verification.
            nonce (bytes): fixed 14-byte value combined with the
                per-message sdls_counter into the 16-byte block used to
                recompute the CMAC tag. Must match sdls_authentication's
                own nonce out-of-band, or every tag fails to verify.

        Raises:
            TypeError: nonce is not bytes-like.
            ValueError: nonce is not exactly 14 bytes.
        """
        gr.basic_block.__init__(self,
            name="sdls_authentication_verify",
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
        """
        Args:
            dict_msg (pmt_dict): PDU metadata dict.

        Returns:
            bytes | None: 32-byte AES-256 key, or None if `auth_key` is
                absent, malformed, or the wrong length.
        """
        if not pmt.dict_has_key(dict_msg, pmt.intern("auth_key")):
            self.logger.error(f"Received dict message with missing auth_key: {dict_msg}")
            return None

        key = pmt.dict_ref(dict_msg, pmt.intern("auth_key"), pmt.PMT_NIL)

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
            return None

        if len(secret) != 32:
            self.logger.error(f"auth_key length must be exactly 32 bytes (AES-256): {len(secret)}")
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

    def _build_ctr_counter_block(self, counter: int) -> bytes:
        return self.nonce + counter.to_bytes(2, byteorder="big", signed=False)

    def _split_payload_tag(self, payload_bytes: bytes) -> tuple[bytes, bytes] | None:
        if len(payload_bytes) < TAG_LEN:
            self.logger.error("Payload too short to contain authentication tag.")
            return None

        return payload_bytes[:-TAG_LEN], payload_bytes[-TAG_LEN:]

    def _pmt_dict_get_int(self, meta, key, default=None):
        """
        Args:
            meta (pmt_dict): PMT dict to read from.
            key (str): key to look up.
            default: value to return if meta isn't a dict, the key is
                absent, or the value can't be converted to int.

        Returns:
            int: the value at `key` converted to a Python int, or
                `default`.
        """
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
        """
        Args:
            encap_meta (pmt_dict): the `encapsulation_header` metadata
                dict ccsds_reader publishes (parsed fields, not the
                original bytes).

        Returns:
            bytes | None: the reconstructed raw encapsulation-header
                bytes (must match ccsds_reader.encapsulation_header()'s
                own bit-packing), or None if length_of_length or a
                required field is missing.
        """
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
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (must include
                `auth_key` and `sdls_counter` unless authentication_state
                is False) and u8vector payload - either
                `payload || 16-byte tag`, or ciphertext alone with the
                tag supplied via `dict_msg["sdls"]["security_trailer"]`.

        Publishes:
            "out" (pmt_pair): PDU with `auth_key` removed from metadata
                and the tag stripped from the payload. If
                authentication_state is False, republishes the input
                unchanged instead (after the same shape checks).

        Drops when:
            - msg is not a PDU pair (error - malformed input, this block sits downstream of ccsds_reader)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - auth_key is absent, malformed, or not 32 bytes (error - same)
            - sdls_counter is absent, non-integer, or out of range (error - same)
            - the payload is too short to contain a tag (error - same)
            - the authentication tag fails to verify (error - same)
            - tag reconstruction, verification, or publishing fails, in either the verification or the authentication_state=False passthrough path (error - same)
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

            # If authentication state is False, pass through the message unmodified
            if not self.authentication_state:
                self.message_port_pub(pmt.intern("out"), msg)
                return

            # If authentication state is True, perform verification
            secret_bytes = self._extract_secret(dict_msg)
            if secret_bytes is None:
                self.logger.error(f"Failed to extract valid authentication key from message: {dict_msg}")
                return

            counter = self._extract_counter(dict_msg)
            if counter is None:
                self.logger.error(f"Failed to extract valid sdls_counter from message: {dict_msg}")
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
                self.logger.error("Authentication tag verification failed; dropping message.")
                return

            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("auth_key"))
            out_payload = pmt.init_u8vector(len(out_payload_bytes), list(out_payload_bytes))
            msg_out = pmt.cons(dict_msg, out_payload)
            self.message_port_pub(pmt.intern("out"), msg_out)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to verify or publish message: {exc}")
            return
