#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from gnuradio import gr
import pmt

class Injectdb(gr.basic_block):
    """
    docstring for block Injectdb
    """
    def __init__(self):
        gr.basic_block.__init__(self,
            name="Inject DB",
            in_sig=None,
            out_sig=None)
        

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Database ports
        self.message_port_register_in(pmt.intern("db_callback"))
        self.message_port_register_out(pmt.intern("db_call"))

        # Handlers
        self.set_msg_handler(pmt.intern("in"), self.send_db_call)
        self.set_msg_handler(pmt.intern("db_callback"), self.send_msg_out)


    def _is_integer_pmt(self, value) -> bool:
        return pmt.is_integer(value) or pmt.is_uint64(value)


    def _check_key_type(self, dict_msg, key: str, value, expected_type: str) -> bool:
        if expected_type == "int":
            if self._is_integer_pmt(value):
                return True
            self.logger.warn(
                f"Received PDU with non-integer value for key '{key}' in metadata (expected integer or uint64): {dict_msg}"
            )
            return False

        if expected_type == "bool":
            if pmt.is_bool(value):
                return True
            self.logger.warn(
                f"Received PDU with non-boolean value for key '{key}' in metadata (expected PMT boolean): {dict_msg}"
            )
            return False

        if expected_type == "int_or_nil":
            if pmt.eqv(value, pmt.PMT_NIL) or self._is_integer_pmt(value):
                return True
            self.logger.warn(
                f"Received PDU with invalid value for key '{key}' in metadata (expected PMT_NIL, integer, or uint64): {dict_msg}"
            )
            return False

        if expected_type == "secret_or_nil":
            if pmt.eqv(value, pmt.PMT_NIL):
                return True
            if pmt.is_symbol(value):
                return True
            self.logger.warn(
                f"Received PDU with invalid value for key '{key}' in metadata (expected PMT_NIL or symbol hex string): {dict_msg}"
            )
            return False

        raise ValueError(f"Unsupported expected type '{expected_type}' for key '{key}'")


    def _extract_pdu(self, msg):

        if not pmt.is_pair(msg):
            self.logger.warn(f"Received non-PDU message: {msg}")
            return None # Early exit if message is not a pair (dict, payload)
        
        dict_msg = pmt.car(msg)
        payload_u8vector = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.warn(f"Received message with non-dict metadata: {msg}")
            return None # Early exit if metadata is not a dict
        
        if not pmt.is_u8vector(payload_u8vector):
            self.logger.warn(f"Received message with non-u8vector payload: {msg}")
            return None # Early exit if payload is not a u8vector

        return (dict_msg, payload_u8vector)


    def _check_keys(self, dict_msg, key_specs) -> bool:
        for key, expected_type in key_specs.items():
            pmt_key = pmt.intern(key)
            if not pmt.dict_has_key(dict_msg, pmt_key):
                self.logger.warn(f"Received PDU with missing required key '{key}' in metadata: {dict_msg}")
                return False # Early exit if any required key is missing

            value = pmt.dict_ref(dict_msg, pmt_key, pmt.PMT_NIL)
            if not self._check_key_type(dict_msg, key, value, expected_type):
                return False # Early exit if any required key value is not an integer
            
        return True


    def send_db_call(self, msg):
        # For testing, we will just send a fixed query to the database client.

        if not pmt.is_pair(msg):
            self.logger.warn(f"Received non-PDU message from input: {msg}")
            return # Early exit if message is not a pair (dict, payload)

        extracted = self._extract_pdu(msg)
        if extracted is None:
            # Already logged the specific missing/invalid key(s) in the _extract_pdu function
            return # Early exit if message is not a valid PDU
        dict_msg, payload_u8vector = extracted
        
        # Check if keys are there to ask the database for the right material.
        if not self._check_keys(
            dict_msg,
            {
                "scid": "int",
                "spi": "int",
                "bypass": "bool",
                "control": "bool",
            },
        ):
            # Already logged the specific missing/invalid key(s) in the _check_keys function
            return # Early exit if required keys are missing or invalid
        

        self.message_port_pub(pmt.intern("db_call"), msg)



    def send_msg_out(self, msg):

        if not pmt.is_pair(msg):
            self.logger.warn(f"Received non-PDU message from database: {msg}")
            return # Early exit if message is not a pair (dict, payload)

        extracted = self._extract_pdu(msg)
        if extracted is None:
            # Already logged the specific missing/invalid key(s) in the _extract_pdu function
            return # Early exit if message is not a valid PDU
        dict_msg, payload_u8vector = extracted
        
        # Check if keys are there to ask the database for the right material.
        if not self._check_keys(
            dict_msg,
            {
                "scid": "int",
                "spi": "int",
                "bypass": "bool",
                "control": "bool",
                "auth_key": "secret_or_nil",
                "crypt_key": "secret_or_nil",
                "vcid": "int",
                "vcid_counter": "int",
                "sdls_counter": "int",
            },
        ):
            # Already logged the specific missing/invalid key(s) in the _check_keys function
            return # Early exit if required keys are missing or invalid
        
        self.message_port_pub(pmt.intern("out"), msg)

