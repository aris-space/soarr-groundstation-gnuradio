#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from gnuradio import gr
import pmt

class inject_db(gr.basic_block):
    """
    docstring for block inject_db
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

        self._pending_meta = None
        self._pending_payload = None


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
            if self._is_integer_pmt(value):
                int_value = int(pmt.to_uint64(value)) if pmt.is_uint64(value) else int(pmt.to_long(value))
                if int_value in (0, 1):
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

    def _resolve_key(self, dict_msg, key: str):
        pmt_key = pmt.intern(key)
        if pmt.dict_has_key(dict_msg, pmt_key):
            return pmt.dict_ref(dict_msg, pmt_key, pmt.PMT_NIL)

        if key in {"scid", "vcid", "bypass", "control", "vcid_counter"}:
            telecommand = pmt.dict_ref(dict_msg, pmt.intern("telecommand"), pmt.PMT_NIL)
            if pmt.is_dict(telecommand):
                tc_header = pmt.dict_ref(telecommand, pmt.intern("tc_header"), pmt.PMT_NIL)
                if pmt.is_dict(tc_header):
                    tc_key = key
                    if key == "bypass":
                        tc_key = "bypass_flag"
                    if key == "control":
                        tc_key = "control_flag"
                    if key == "vcid_counter":
                        tc_key = "vcid_counter"
                    return pmt.dict_ref(tc_header, pmt.intern(tc_key), pmt.PMT_NIL)

        if key in {"spi", "sdls_counter"}:
            sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
            if pmt.is_dict(sdls):
                sdls_header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)
                if pmt.is_dict(sdls_header):
                    sdls_key = key
                    if key == "spi":
                        sdls_key = "spi"
                    if key == "sdls_counter":
                        sdls_key = "sdls_counter"
                    return pmt.dict_ref(sdls_header, pmt.intern(sdls_key), pmt.PMT_NIL)

        return pmt.PMT_NIL

    def _should_merge_key(self, dict_msg, key: str) -> bool:
        pmt_key = pmt.intern(key)
        if pmt.dict_has_key(dict_msg, pmt_key):
            return False
        return pmt.eqv(self._resolve_key(dict_msg, key), pmt.PMT_NIL)

    def _ensure_nested(self, dict_msg, group_key, child_key):
        group = pmt.dict_ref(dict_msg, pmt.intern(group_key), pmt.PMT_NIL)
        if not pmt.is_dict(group):
            group = pmt.make_dict()
            dict_msg = pmt.dict_add(dict_msg, pmt.intern(group_key), group)
        child = pmt.dict_ref(group, pmt.intern(child_key), pmt.PMT_NIL)
        if not pmt.is_dict(child):
            child = pmt.make_dict()
            group = pmt.dict_add(group, pmt.intern(child_key), child)
            dict_msg = pmt.dict_add(dict_msg, pmt.intern(group_key), group)
        return dict_msg, group, child

    def _merge_metadata(self, primary, secondary):
        if not pmt.is_dict(primary) or not pmt.is_dict(secondary):
            return primary

        merged = primary
        merged_keys = []

        try:
            py_dict = pmt.to_python(secondary)
            if isinstance(py_dict, dict):
                for key, value in py_dict.items():
                    key_str = str(key)
                    if not self._should_merge_key(merged, key_str):
                        continue
                    merged = self._merge_key_into_nested(merged, key_str, self._python_to_pmt(value))
                    merged_keys.append(key_str)
                return merged
        except Exception:
            pass

        try:
            items = pmt.dict_items(secondary)
        except Exception:
            return merged

        while not pmt.is_null(items):
            item = pmt.car(items)
            items = pmt.cdr(items)
            key = pmt.car(item)
            value = pmt.cdr(item)
            key_str = pmt.symbol_to_string(key) if pmt.is_symbol(key) else None
            if key_str is None or not self._should_merge_key(merged, key_str):
                continue
            merged = self._merge_key_into_nested(merged, key_str, value)
            merged_keys.append(key_str)

        if merged_keys:
            self.logger.debug(f"Merged DB keys into nested metadata: {sorted(set(merged_keys))}")

        return merged

    def _merge_key_into_nested(self, dict_msg, key: str, value):
        if key in {"scid", "vcid", "bypass", "control", "vcid_counter"}:
            dict_msg, telecommand, tc_header = self._ensure_nested(dict_msg, "telecommand", "tc_header")
            tc_key = key
            if key == "bypass":
                tc_key = "bypass_flag"
            if key == "control":
                tc_key = "control_flag"
            tc_header = pmt.dict_add(tc_header, pmt.intern(tc_key), value)
            telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("telecommand"), telecommand)
            return dict_msg

        if key in {"spi", "sdls_counter"}:
            dict_msg, sdls, sdls_header = self._ensure_nested(dict_msg, "sdls", "security_header")
            sdls_key = key
            sdls_header = pmt.dict_add(sdls_header, pmt.intern(sdls_key), value)
            sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("sdls"), sdls)
            return dict_msg

        return pmt.dict_add(dict_msg, pmt.intern(key), value)

    def _python_to_pmt(self, value):
        if isinstance(value, bool):
            return pmt.from_bool(value)
        if isinstance(value, int):
            return pmt.from_long(value)
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            return pmt.init_u8vector(len(b), list(b))
        if isinstance(value, str):
            return pmt.intern(value)
        if isinstance(value, dict):
            out = pmt.make_dict()
            for key, subval in value.items():
                out = pmt.dict_add(out, pmt.intern(str(key)), self._python_to_pmt(subval))
            return out
        return pmt.intern(str(value))


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
            value = self._resolve_key(dict_msg, key)
            if pmt.eqv(value, pmt.PMT_NIL):
                self.logger.warn(f"Received PDU with missing required key '{key}' in metadata: {dict_msg}")
                return False
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
        

        msg = pmt.cons(dict_msg, payload_u8vector)
        self._pending_meta = dict_msg
        self._pending_payload = payload_u8vector
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

        if self._pending_meta is not None:
            dict_msg = self._merge_metadata(self._pending_meta, dict_msg)
            if self._pending_payload is not None:
                payload_u8vector = self._pending_payload
            self._pending_meta = None
            self._pending_payload = None

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
        
        msg = pmt.cons(dict_msg, payload_u8vector)
        self.message_port_pub(pmt.intern("out"), msg)
        self.logger.info(f"OK")
        

