#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from collections import OrderedDict

from gnuradio import gr
import pmt

NULLABLE_TYPES = frozenset({"int_or_nil", "secret_or_nil"})

# Metadata key carrying a query's id on db_call; the database echoes it on
# db_callback so each response is paired with its own request.
REQUEST_ID_KEY = "db_request_id"

# Per-frame counters. On TX the database assigns and increments them, so its
# values replace the request's (a counter reused under the same key would
# break AES-CTR, and a repeated frame sequence number is rejected on board).
# On RX they were read from the received frame and must be kept.
DB_OWNED_KEYS = frozenset({"sdls_counter", "vcid_counter"})
ROLES = ("tx", "rx")


class inject_db(gr.basic_block):
    """
    Query a key/security-material database via a db_call/db_callback
    side-channel and merge the response into a PDU's metadata before
    forwarding it downstream.

    Flow:
    - Receive a PDU on `in`, validate it has the fields needed to query
      the database, store it as a pending request under a new request id,
      forward it on `db_call` with that id added as `db_request_id`
    - Receive the query response on `db_callback`, look up the pending
      request by the echoed `db_request_id` (a response without one is
      paired with the oldest pending request), merge it with that
      request's metadata (the request's own keys win; with role "tx" the
      database's sdls_counter/vcid_counter replace the request's), validate the
      merged result has every field downstream blocks need, publish on
      `out` paired with the request's original payload

    Several requests may be in flight at once. Requests the database never
    answers stay pending until MAX_PENDING_REQUESTS newer ones push them
    out.
    """

    MAX_PENDING_REQUESTS = 256

    def __init__(self, role:str="tx"):
        """
        Args:
            role (str): where this instance sits, which decides who owns the
                per-frame counters (sdls_counter, vcid_counter):
                "tx" - the database assigns them; its values replace any
                    the request carries (e.g. data_creator's fixed ones).
                "rx" - they were read from the received frame (IV, frame
                    sequence number) and are kept; the database's are
                    ignored, so a lost frame can't shift them.

        Raises:
            ValueError: role is not "tx" or "rx".
        """
        if role not in ROLES:
            raise ValueError(f"role must be 'tx' or 'rx', got {role!r}.")
        self.role = role
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

        # request id -> (metadata dict, payload), oldest first
        self._pending = OrderedDict()
        self._next_request_id = 0
        self.logger.info(
            "role tx: counters from the database" if role == "tx"
            else "role rx: counters from the received frame"
        )

    def _is_integer_pmt(self, value) -> bool:
        """
        Args:
            value (pmt_any): value to check.

        Returns:
            bool: True if value is a PMT integer or uint64.
        """
        return pmt.is_integer(value) or pmt.is_uint64(value)

    def _check_key_type(self, dict_msg, key: str, value, expected_type: str) -> bool:
        """
        Args:
            dict_msg (pmt_dict): metadata dict, used only for logging context.
            key (str): metadata key being checked, used only for logging context.
            value (pmt_any): the already-resolved value to type-check.
            expected_type (str): one of "int", "bool", "int_or_nil", "secret_or_nil".

        Returns:
            bool: True if value matches expected_type; otherwise logs at
                error and returns False.

        Raises:
            ValueError: expected_type is not one of the four supported values.
        """
        if expected_type == "int":
            if self._is_integer_pmt(value):
                return True
            self.logger.error(
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
            self.logger.error(
                f"Received PDU with non-boolean value for key '{key}' in metadata (expected PMT boolean): {dict_msg}"
            )
            return False

        if expected_type == "int_or_nil":
            if pmt.eqv(value, pmt.PMT_NIL) or self._is_integer_pmt(value):
                return True
            self.logger.error(
                f"Received PDU with invalid value for key '{key}' in metadata (expected PMT_NIL, integer, or uint64): {dict_msg}"
            )
            return False

        if expected_type == "secret_or_nil":
            if pmt.eqv(value, pmt.PMT_NIL):
                return True
            if pmt.is_symbol(value):
                return True
            self.logger.error(
                f"Received PDU with invalid value for key '{key}' in metadata (expected PMT_NIL or symbol hex string): {dict_msg}"
            )
            return False

        raise ValueError(f"Unsupported expected type '{expected_type}' for key '{key}'")

    def _resolve_key(self, dict_msg, key: str):
        """
        Args:
            dict_msg (pmt_dict): top-level PDU metadata dict.
            key (str): key to resolve, checked at the top level first,
                falling back to the relevant nested path
                (`telecommand.tc_header` or `sdls.security_header`) only
                if the top-level key is absent.

        Returns:
            pmt_any: the resolved value, or `pmt.PMT_NIL` if the key is
                absent both at the top level and (where applicable) the
                nested path.
        """
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
                    return pmt.dict_ref(tc_header, pmt.intern(tc_key), pmt.PMT_NIL)

        if key in {"spi", "sdls_counter"}:
            sdls = pmt.dict_ref(dict_msg, pmt.intern("sdls"), pmt.PMT_NIL)
            if pmt.is_dict(sdls):
                sdls_header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)
                if pmt.is_dict(sdls_header):
                    return pmt.dict_ref(sdls_header, pmt.intern(key), pmt.PMT_NIL)

        return pmt.PMT_NIL

    def _should_merge_key(self, dict_msg, key: str) -> bool:
        """
        Args:
            dict_msg (pmt_dict): top-level PDU metadata dict to check against.
            key (str): key to check.

        Returns:
            bool: True if key is genuinely absent everywhere (top level
                and, where applicable, the nested path) in dict_msg.
        """
        pmt_key = pmt.intern(key)
        if pmt.dict_has_key(dict_msg, pmt_key):
            return False
        return pmt.eqv(self._resolve_key(dict_msg, key), pmt.PMT_NIL)

    def _ensure_nested(self, dict_msg, group_key, child_key):
        """
        Args:
            dict_msg (pmt_dict): dict to ensure the nested path exists in.
            group_key (str): outer key (e.g. "telecommand").
            child_key (str): inner key under group_key (e.g. "tc_header").

        Returns:
            tuple[pmt_dict, pmt_dict, pmt_dict]: (dict_msg, group, child) -
                dict_msg with group_key/child_key present (creating empty
                dicts if either was missing or not a dict), and the group/
                child dicts themselves for the caller to add keys into.
        """
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
        """
        Args:
            primary (pmt_dict): metadata dict whose existing keys always win.
            secondary (pmt_dict): metadata dict to merge keys from - only
                keys genuinely absent from primary (top level and, where
                applicable, the nested path) are merged in, except the
                DB_OWNED_KEYS counters with role "tx": those always
                replace primary's (a top-level copy in primary is removed).

        Returns:
            pmt_dict: primary, with secondary's absent keys merged into
                the relevant nested location (see _merge_key_into_nested).
                If either argument isn't a dict, returns primary unchanged.
        """
        if not pmt.is_dict(primary) or not pmt.is_dict(secondary):
            return primary

        merged = primary
        merged_keys = []

        items = pmt.dict_items(secondary)
        while not pmt.is_null(items):
            item = pmt.car(items)
            items = pmt.cdr(items)
            key = pmt.car(item)
            value = pmt.cdr(item)
            key_str = pmt.symbol_to_string(key) if pmt.is_symbol(key) else None
            if key_str in DB_OWNED_KEYS and self.role == "tx":
                # Drop a top-level copy from the request, which downstream
                # blocks would read before the nested value set below.
                if pmt.dict_has_key(merged, key):
                    merged = pmt.dict_delete(merged, key)
                merged = self._merge_key_into_nested(merged, key_str, value)
                merged_keys.append(key_str)
                continue
            if key_str is None or not self._should_merge_key(merged, key_str):
                continue
            merged = self._merge_key_into_nested(merged, key_str, value)
            merged_keys.append(key_str)

        if merged_keys:
            self.logger.debug(f"Merged DB keys into nested metadata: {sorted(set(merged_keys))}")

        return merged

    def _merge_key_into_nested(self, dict_msg, key: str, value):
        """
        Args:
            dict_msg (pmt_dict): metadata dict to merge into.
            key (str): key being merged.
            value (pmt_any): value to store at key.

        Returns:
            pmt_dict: dict_msg with key/value added at the location
                downstream blocks expect it: `telecommand.tc_header` for
                scid/vcid/bypass(→bypass_flag)/control(→control_flag)/
                vcid_counter, `sdls.security_header` for spi/sdls_counter,
                top level for everything else.
        """
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
            sdls_header = pmt.dict_add(sdls_header, pmt.intern(key), value)
            sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("sdls"), sdls)
            return dict_msg

        return pmt.dict_add(dict_msg, pmt.intern(key), value)

    def _extract_pdu(self, msg, source="message", require_u8vector_payload=True):
        """
        Args:
            msg (pmt_any): value expected to be a PDU pair.
            source (str): human-readable origin, used only in log messages
                (e.g. "input", "database").
            require_u8vector_payload (bool): if False, a non-u8vector
                payload (e.g. PMT_NIL) is returned as-is instead of being
                rejected - used for the db_callback path, where a
                non-u8vector payload is expected whenever
                db_client(forward_body=False) is in use, and gets
                replaced by the pending request's own payload before
                publishing (see send_msg_out).

        Returns:
            tuple[pmt_dict, pmt_any] | None: (metadata dict, payload) if
                msg is a valid PDU pair with dict metadata and (unless
                require_u8vector_payload is False) a u8vector payload;
                otherwise logs at error and returns None.
        """
        if not pmt.is_pair(msg):
            self.logger.error(f"Received non-PDU {source}: {msg}")
            return None

        dict_msg = pmt.car(msg)
        payload = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.error(f"Received {source} with non-dict metadata: {msg}")
            return None

        if require_u8vector_payload and not pmt.is_u8vector(payload):
            self.logger.error(f"Received {source} with non-u8vector payload: {msg}")
            return None

        return (dict_msg, payload)

    def _check_keys(self, dict_msg, key_specs) -> bool:
        """
        Args:
            dict_msg (pmt_dict): metadata dict to validate.
            key_specs (dict[str, str]): key name -> expected type (one of
                "int", "bool", "int_or_nil", "secret_or_nil").

        Returns:
            bool: True if every key resolves (see _resolve_key) to a value
                matching its expected type. A key resolving to PMT_NIL is
                only treated as missing (logged at error, returns False)
                for non-nullable types - a nullable type (int_or_nil,
                secret_or_nil) defers the PMT_NIL/absent decision entirely
                to _check_key_type, since for those types "absent" and
                "present but nil" are both valid.
        """
        for key, expected_type in key_specs.items():
            value = self._resolve_key(dict_msg, key)
            if pmt.eqv(value, pmt.PMT_NIL) and expected_type not in NULLABLE_TYPES:
                self.logger.error(f"Received PDU with missing required key '{key}' in metadata: {dict_msg}")
                return False
            if not self._check_key_type(dict_msg, key, value, expected_type):
                return False

        return True

    def send_db_call(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.
                Metadata must include `scid`/`spi` (int) and `bypass`/
                `control` (bool), each checked at the top level first,
                falling back to the nested `telecommand.tc_header`/
                `sdls.security_header` path (see _resolve_key). This is a
                fixed, simplified query, not necessarily every field a
                real database lookup would need.

        Publishes:
            "db_call" (pmt_pair): the input PDU with `db_request_id`
                (uint64, unique per request) added to its metadata. Also
                stores the unmodified input as a pending request under that
                id (self._pending) for send_msg_out to merge with the
                matching db_callback response. If MAX_PENDING_REQUESTS are
                already pending, the oldest is discarded (logged at warn).

        Drops when:
            - msg is not a PDU pair, metadata is not a dict, or payload is not a u8vector (error - malformed input, not raw RF noise)
            - a required key is missing or the wrong type (error - same)
            - an internal failure occurs while building or publishing the query (error - same)
        """
        try:
            extracted = self._extract_pdu(msg, source="input")
            if extracted is None:
                return
            dict_msg, payload_u8vector = extracted

            if not self._check_keys(
                dict_msg,
                {
                    "scid": "int",
                    "spi": "int",
                    "bypass": "bool",
                    "control": "bool",
                },
            ):
                return

            request_id = self._next_request_id
            self._next_request_id += 1
            query_meta = pmt.dict_add(dict_msg, pmt.intern(REQUEST_ID_KEY), pmt.from_uint64(request_id))
            self.message_port_pub(pmt.intern("db_call"), pmt.cons(query_meta, payload_u8vector))

            # Stored after publishing: the response is handled on this
            # block's own thread, so it can't arrive before this returns.
            if len(self._pending) >= self.MAX_PENDING_REQUESTS:
                dropped_id, _ = self._pending.popitem(last=False)
                self.logger.warn(
                    f"{self.MAX_PENDING_REQUESTS} database requests pending; discarding the oldest "
                    f"(db_request_id {dropped_id}), which was never answered."
                )
            self._pending[request_id] = (dict_msg, payload_u8vector)
        except Exception as exc:
            self.logger.error(f"Failed to build or publish db_call: {exc}")

    def send_msg_out(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and payload - db_client's
                query response. The payload need not be a u8vector (e.g.
                PMT_NIL, from `db_client(forward_body=False)`) as long as
                a pending request exists to supply one instead - see
                Publishes. Metadata must include everything send_db_call
                requires, plus `auth_key`/`crypt_key` (symbol hex string
                or PMT_NIL) and `vcid`/`vcid_counter`/`sdls_counter` (int).

        Publishes:
            "out" (pmt_pair): this PDU's metadata merged with the metadata
                of the pending request it answers (the request's own keys
                win, except the counters - see _merge_metadata), paired with that
                request's original payload. The request is the one whose id
                matches this PDU's `db_request_id`, or the oldest pending
                one if `db_request_id` is absent. If no request is pending,
                publishes this PDU's own metadata and payload. The
                `db_request_id` key itself is never forwarded.

        Drops when:
            - msg is not a PDU pair, or metadata is not a dict (error - malformed input, not raw RF noise)
            - `db_request_id` is not an integer, or matches no pending request (unknown, already answered, or discarded as too old) (error - same)
            - payload is not a u8vector and no pending request exists to supply one instead (error - same)
            - the merged metadata is missing a required key or has the wrong type for one (error - same)
            - an internal failure occurs while merging, building, or publishing the result (error - same)
        """
        try:
            extracted = self._extract_pdu(msg, source="message from database", require_u8vector_payload=False)
            if extracted is None:
                return
            dict_msg, payload = extracted

            request_id_key = pmt.intern(REQUEST_ID_KEY)
            if pmt.dict_has_key(dict_msg, request_id_key):
                request_id_pmt = pmt.dict_ref(dict_msg, request_id_key, pmt.PMT_NIL)
                dict_msg = pmt.dict_delete(dict_msg, request_id_key)
                if not self._is_integer_pmt(request_id_pmt):
                    self.logger.error(f"Received message from database with non-integer {REQUEST_ID_KEY}: {msg}")
                    return
                request_id = (
                    int(pmt.to_uint64(request_id_pmt)) if pmt.is_uint64(request_id_pmt) else int(pmt.to_long(request_id_pmt))
                )
                pending = self._pending.pop(request_id, None)
                if pending is None:
                    self.logger.error(
                        f"Received message from database for {REQUEST_ID_KEY} {request_id}, which matches no pending "
                        "request (unknown, already answered, or discarded as too old)."
                    )
                    return
            elif self._pending:
                _, pending = self._pending.popitem(last=False)
            else:
                pending = None

            if pending is not None:
                pending_meta, pending_payload = pending
                dict_msg = self._merge_metadata(pending_meta, dict_msg)
                payload = pending_payload
            elif not pmt.is_u8vector(payload):
                # No pending request to fall back on, so the callback's
                # own payload must be usable on its own.
                self.logger.error(f"Received message from database with non-u8vector payload and no pending request: {msg}")
                return

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
                return

            out_msg = pmt.cons(dict_msg, payload)
            self.message_port_pub(pmt.intern("out"), out_msg)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to merge, build, or publish message: {exc}")

