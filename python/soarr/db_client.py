#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
from pathlib import Path
from gnuradio import gr
import pmt

try:
    import yaml
except ImportError:
    yaml = None

# SDLS counter is transmitted as the 2-byte IV in the SDLS security header
# (sdls_header), so it is modeled as uint16.
SDLS_COUNTER_MAX = 0xFFFF
VCID_COUNTER_MAX = 0xFF

class db_client(gr.basic_block):
    """Database client for unified SDLS/TC material lookup.

    Modes:
    - type=0: in-memory dummy database with configurable defaults
    - type=1: local YAML-backed database
    - type=2: remote real DB (stub, not implemented yet)
    """

    def __init__(
        self,
        type: int = 0x0,
        ip: str = "127.0.0.1",
        port: int = 80,
        yaml_path: str = "",
        forward_body: bool = True,
        auto_reset_counters: bool = False,
        scid: int = 0x155,
        spi: int = 1,
        vcid: int = 0x12,
        crypt_key: str = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF",
        auth_key: str = "FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100",
        sdls_counter: int = 0,
        vcid_counter: int = 0,
        key_state_enc: str = "active",
        key_state_auth: str = "active",
    ):
        """
        Args:
            type (int): 0=dummy, 1=local YAML, 2=remote DB (unimplemented
                stub). An unrecognized value also falls back to dummy.
            ip (str): reserved for type=2; unused.
            port (int): reserved for type=2; unused.
            yaml_path (str): path to the YAML file for type=1.
            forward_body (bool): if True, echo the query's u8vector
                payload back on db_callback; otherwise PMT_NIL.
            auto_reset_counters (bool): type=0 only - if True,
                sdls_counter resets to 0 after its max value is served
                (reusing AES-CTR counters: test use only). vcid_counter
                always wraps 255 -> 0, independent of this flag.
            scid (int): dummy-mode entry's SCID.
            spi (int): dummy-mode entry's SPI.
            vcid (int): dummy-mode entry's VCID.
            crypt_key (str): dummy-mode entry's encryption key (hex string).
            auth_key (str): dummy-mode entry's authentication key (hex string).
            sdls_counter (int): dummy-mode entry's initial SDLS counter.
            vcid_counter (int): dummy-mode entry's initial VCID counter.
            key_state_enc (str): dummy-mode entry's encryption key state.
            key_state_auth (str): dummy-mode entry's authentication key state.

        Raises:
            TypeError | ValueError: type or port isn't convertible to
                int. Every other parameter is coerced defensively (via
                _to_int/_to_bool or str()) and cannot raise here.
        """
        gr.basic_block.__init__(self,
                name="db_client",
                in_sig=None,
            out_sig=None)


        # 0=dummy, 1=local yaml, 2=remote real DB (stub)
        self.type = int(type)

        # Reserved for remote DB mode.
        self.ip = str(ip)
        self.port = int(port)

        # Optional path for local YAML mode.
        self.yaml_path = str(yaml_path) if yaml_path else ""
        self.forward_body = self._to_bool(forward_body, default=True)
        # If true, dummy-mode counters at max will be reset to 0 after being served
        self.auto_reset_counters = self._to_bool(auto_reset_counters, default=False)

        # Dummy-mode entry fields.
        self.dummy_scid = self._to_int(scid, default=0x155)
        self.dummy_spi = self._to_int(spi, default=1)
        self.dummy_vcid = self._to_int(vcid, default=0x12)
        self.dummy_crypt_key = str(crypt_key)
        self.dummy_auth_key = str(auth_key)
        self.dummy_sdls_counter = self._to_int(sdls_counter, default=0)
        self.dummy_vcid_counter = self._to_int(vcid_counter, default=0)
        self.dummy_key_state_enc = str(key_state_enc)
        self.dummy_key_state_auth = str(key_state_auth)

        # In-memory structure indexed as: {"<SCID>": {"<SPI>": entry}}
        self._db = {}
        self._init_database()

        # Unified DB lookup ports.
        self.message_port_register_in(pmt.intern("db_call"))
        self.message_port_register_out(pmt.intern("db_callback"))

        self.set_msg_handler(pmt.intern("db_call"), self.make_db_call)

    def _init_database(self):
        if self.type == 0:
            self._db = self._dummy_db()
            return

        if self.type == 1:
            self._db = self._load_yaml_db()
            return

        # type=2 not implemented yet; keep empty db and log.
        if self.type == 2:
            self.logger.error("Remote DB mode (type=2) is not implemented yet. Returning fallback data.")
            self._db = self._dummy_db()
            return

        self.logger.error(f"Unknown db type '{self.type}'. Falling back to dummy mode.")
        self._db = self._dummy_db()

    def _dummy_db(self):
        # Build a single configurable SCID/SPI dummy entry for rapid testing.
        scid = self.dummy_scid
        spi = self.dummy_spi
        sdls_counter = max(0, min(self.dummy_sdls_counter, SDLS_COUNTER_MAX))
        vcid_counter = max(0, min(self.dummy_vcid_counter, VCID_COUNTER_MAX))

        return {
            str(scid): {
                str(spi): {
                    "SCID": scid,
                    "SPI": spi,
                    "VCID": self.dummy_vcid,
                    "crypt_key": self.dummy_crypt_key,
                    "auth_key": self.dummy_auth_key,
                    "sdls_counter": sdls_counter,
                    "vcid_counter": vcid_counter,
                    "key_state": {
                        "enc": self.dummy_key_state_enc,
                        "auth": self.dummy_key_state_auth,
                    },
                }
            }
        }

    def _normalize_entry(self, item, fallback_scid=None, fallback_spi=None, fallback_vcid=0):
        if not isinstance(item, dict):
            return None

        scid_val = self._to_int(item.get("SCID", fallback_scid), default=-1)
        spi_val = self._to_int(item.get("SPI", fallback_spi), default=-1)
        vcid_val = self._to_int(item.get("VCID", fallback_vcid), default=0)

        if scid_val < 0 or spi_val < 0:
            return None

        return {
            "SCID": scid_val,
            "SPI": spi_val,
            "VCID": vcid_val,
            "crypt_key": str(item.get("crypt_key", item.get("encryption_key", ""))),
            "auth_key": str(item.get("auth_key", item.get("authentication_key", ""))),
            "sdls_counter": self._to_int(item.get("sdls_counter"), default=0),
            "vcid_counter": self._to_int(item.get("vcid_counter"), default=0),
            "key_state": item.get("key_state", {}),
        }

    def _load_yaml_db(self):
        if yaml is None:
            self.logger.error("PyYAML is not installed; cannot use local YAML mode.")
            return self._dummy_db()

        if not self.yaml_path:
            self.logger.error("YAML mode selected but no yaml_path provided. Falling back to dummy DB.")
            return self._dummy_db()

        path = Path(self.yaml_path)
        if not path.exists():
            self.logger.error(f"YAML database file not found: {path}. Falling back to dummy DB.")
            return self._dummy_db()

        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        except Exception as exc:
            self.logger.error(f"Failed to parse YAML database '{path}': {exc}. Falling back to dummy DB.")
            return self._dummy_db()

        if not isinstance(loaded, dict):
            self.logger.error("YAML database root must be a mapping. Falling back to dummy DB.")
            return self._dummy_db()

        # Accepted layouts:
        # - {"entries": {"18": {...}}}
        # - {"18": {...}}
        entries = loaded.get("entries", loaded)
        if not isinstance(entries, dict):
            self.logger.error("YAML database entries must be a mapping. Falling back to dummy DB.")
            return self._dummy_db()

        normalized = {}

        # Accepted entry forms:
        # - Flat: entries: {"id": {SCID, SPI, ...}, ...}
        # - Nested by SCID: entries: {"341": {"1": {...}, "2": {...}}}
        for key, item in entries.items():
            if not isinstance(item, dict):
                continue

            if "SCID" in item and "SPI" in item:
                entry = self._normalize_entry(item)
                if entry is None:
                    continue
                normalized.setdefault(str(entry["SCID"]), {})[str(entry["SPI"])] = entry
                continue

            # Try nested SCID->SPI layout.
            outer_scid = self._to_int(key, default=-1)
            if outer_scid < 0:
                continue

            for inner_key, inner_item in item.items():
                entry = self._normalize_entry(
                    inner_item,
                    fallback_scid=outer_scid,
                    fallback_spi=self._to_int(inner_key, default=-1),
                )
                if entry is None:
                    continue
                normalized.setdefault(str(entry["SCID"]), {})[str(entry["SPI"])] = entry

        if not normalized:
            self.logger.error("No valid entries found in YAML database. Falling back to dummy DB.")
            return self._dummy_db()

        return normalized

    def _to_int(self, value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    def _to_bool(self, value, default=False):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        try:
            sval = str(value).strip().lower()
        except Exception:
            return default
        if sval in ("1", "true", "yes", "on"):
            return True
        if sval in ("0", "false", "no", "off"):
            return False
        return default

    def _pmt_dict_get_bool(self, meta, key, default=None):
        """
        Args:
            meta (pmt_dict): dict to read key from.
            key (str): metadata key to extract.
            default: value returned if meta isn't a dict, key is
                absent/nil, or the value isn't convertible to bool.

        Returns:
            bool | default: the resolved boolean, or default.
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
            return pmt.to_bool(value)
        except Exception:
            return default

    def _pmt_dict_get_int(self, meta, key, default=None):
        """
        Args:
            meta (pmt_dict): dict to read key from.
            key (str): metadata key to extract.
            default: value returned if meta isn't a dict, key is
                absent/nil, or the value isn't convertible to int.

        Returns:
            int | default: the resolved integer, or default.
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
            return default

    def _pmt_dict_get(self, meta, key):
        """
        Args:
            meta (pmt_dict): dict to read key from.
            key (str): metadata key to extract.

        Returns:
            pmt_any: the raw resolved value, or `pmt.PMT_NIL` if meta
                isn't a dict or key is absent.
        """
        if not pmt.is_dict(meta):
            return pmt.PMT_NIL
        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            return pmt.PMT_NIL
        return pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)

    def _resolve_nested(self, meta, path):
        """
        Args:
            meta (pmt_dict): dict to walk.
            path (list[str]): sequence of keys to descend through.

        Returns:
            pmt_any: the value at the end of path, or `pmt.PMT_NIL` if
                any key along the way is absent.
        """
        current = meta
        for key in path:
            current = self._pmt_dict_get(current, key)
            if pmt.eqv(current, pmt.PMT_NIL):
                return pmt.PMT_NIL
        return current

    def _resolve_scid_spi(self, meta):
        """
        Args:
            meta (pmt_dict): top-level query metadata dict.

        Returns:
            tuple[int, int] | tuple[None, None]: scid and spi, each
                resolved independently - top-level first, falling back
                to the relevant nested path (`telecommand.tc_header.scid`
                / `sdls.security_header.spi`) only if that specific key
                is absent at the top level. (None, None) if either ends
                up unresolved.
        """
        tc_header = self._resolve_nested(meta, ["telecommand", "tc_header"])
        sdls_header = self._resolve_nested(meta, ["sdls", "security_header"])

        scid = self._pmt_dict_get_int(meta, "scid", None)
        if scid is None:
            scid = self._pmt_dict_get_int(tc_header, "scid", None)

        spi = self._pmt_dict_get_int(meta, "spi", None)
        if spi is None:
            spi = self._pmt_dict_get_int(sdls_header, "spi", None)

        if scid is None or spi is None:
            return None, None

        return scid, spi

    def _resolve_bypass_control(self, meta):
        """
        Args:
            meta (pmt_dict): top-level query metadata dict.

        Returns:
            tuple[bool, bool]: bypass and control, each resolved
                independently - top-level first, falling back to
                `telecommand.tc_header.<key>` (defaulting to False if
                absent there too) only if that specific key is absent
                at the top level.
        """
        tc_header = self._resolve_nested(meta, ["telecommand", "tc_header"])

        bypass = self._pmt_dict_get_bool(meta, "bypass", None)
        if bypass is None:
            bypass = self._pmt_dict_get_bool(tc_header, "bypass", False)

        control = self._pmt_dict_get_bool(meta, "control", None)
        if control is None:
            control = self._pmt_dict_get_bool(tc_header, "control", False)

        return bypass, control

    def _entry_from_scid_spi(self, meta):
        """
        Args:
            meta (pmt_dict): top-level query metadata dict.

        Returns:
            dict | None: the DB entry for the resolved scid/spi (see
                _resolve_scid_spi), or None if either is unresolved or
                no entry exists for that scid/spi.
        """
        scid, spi = self._resolve_scid_spi(meta)
        if scid is None or spi is None:
            return None

        return self._db.get(str(scid), {}).get(str(spi))

    def _publish(self, port_name, response_meta, response_body=pmt.PMT_NIL):
        """
        Args:
            port_name (str): output port to publish on.
            response_meta (pmt_dict): metadata dict for the outgoing PDU.
            response_body (pmt_any): payload for the outgoing PDU,
                defaults to PMT_NIL.

        Returns: None. Publishes (response_meta . response_body) on port_name.
        """
        out_msg = pmt.cons(response_meta, response_body)
        self.message_port_pub(pmt.intern(port_name), out_msg)

    def _checked_increment(self, value, max_value, counter_name):
        """
        Args:
            value (int): current counter value.
            max_value (int): inclusive upper bound.
            counter_name (str): used only in the raised error message.

        Returns:
            int: value + 1.

        Raises:
            OverflowError: value is already at max_value.
        """
        if value >= max_value:
            raise OverflowError(f"{counter_name} reached max value {max_value}; cannot increment")
        return value + 1

    def _validate_counter(self, value, max_value, counter_name):
        """
        Args:
            value: counter value to validate (converted to int).
            max_value (int): inclusive upper bound.
            counter_name (str): used only in the raised error message.

        Returns:
            int: value, converted to int.

        Raises:
            OverflowError: value is outside [0, max_value].
            TypeError | ValueError: value isn't convertible to int.
        """
        ivalue = int(value)
        if ivalue < 0 or ivalue > max_value:
            raise OverflowError(f"{counter_name} out of range [0, {max_value}]: {ivalue}")
        return ivalue

    def make_db_call(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and any payload.
                Metadata must resolve `scid`/`spi` to a known DB entry
                (see _resolve_scid_spi); `bypass`/`control` are optional,
                echoed back from the query if present (see
                _resolve_bypass_control).

        Publishes:
            "db_callback" (pmt_pair): metadata with the query's
                `scid`/`spi`/`bypass`/`control` echoed back, plus the
                looked-up `vcid`/`crypt_key`/`auth_key`/`sdls_counter`/
                `vcid_counter`, and the query's `db_request_id` if it has
                one (inject_db uses it to pair the response with its
                request). Payload is the query's own payload if
                `forward_body` is True and it's a u8vector, otherwise
                `PMT_NIL`.

        Drops when:
            - msg is not a PDU pair, or metadata is not a dict (error - malformed input, not raw RF noise)
            - scid/spi don't resolve to a known DB entry (error - same)
            - the entry's sdls_counter is exhausted: its max value was
              already served and auto-reset is off, so serving again
              would reuse a (key, counter) pair (error - a new key is
              required)
            - the entry's stored counters are invalid (error - same)
            - an internal failure occurs while building or publishing the response (error - same)
        """
        try:
            if not pmt.is_pair(msg):
                self.logger.error(f"DB query message is not a pair: {msg}")
                return

            meta = pmt.car(msg)
            if not pmt.is_dict(meta):
                self.logger.error("DB query metadata is not a dictionary.")
                return

            entry = self._entry_from_scid_spi(meta)
            if entry is None:
                self.logger.error("DB query missing or unknown SCID/SPI.")
                return

            if entry.get("sdls_counter_exhausted", False):
                self.logger.error(
                    f"sdls_counter exhausted for SCID {entry.get('SCID')} / SPI {entry.get('SPI')}; "
                    "refusing request to avoid (key, counter) reuse. A new key is required."
                )
                return

            try:
                sdls_counter = self._validate_counter(entry.get("sdls_counter", 0), SDLS_COUNTER_MAX, "sdls_counter")
                vcid_counter = self._validate_counter(entry.get("vcid_counter", 0), VCID_COUNTER_MAX, "vcid_counter")
            except (TypeError, ValueError, OverflowError) as exc:
                self.logger.error(f"Invalid counter value: {exc}")
                return

            response_meta = pmt.make_dict()
            # Old inputs - echo back bypass and control from query
            response_meta = pmt.dict_add(response_meta, pmt.intern("scid"), pmt.from_long(int(entry.get("SCID", 0))))
            response_meta = pmt.dict_add(response_meta, pmt.intern("spi"), pmt.from_long(int(entry.get("SPI", 0))))
            bypass, control = self._resolve_bypass_control(meta)
            response_meta = pmt.dict_add(response_meta, pmt.intern("bypass"), pmt.from_bool(bypass))
            response_meta = pmt.dict_add(response_meta, pmt.intern("control"), pmt.from_bool(control))

            # New outputs
            response_meta = pmt.dict_add(response_meta, pmt.intern("vcid"), pmt.from_long(int(entry.get("VCID", 0))))
            response_meta = pmt.dict_add(response_meta, pmt.intern("crypt_key"), pmt.intern(str(entry.get("crypt_key", ""))))
            response_meta = pmt.dict_add(response_meta, pmt.intern("auth_key"), pmt.intern(str(entry.get("auth_key", ""))))
            response_meta = pmt.dict_add(response_meta, pmt.intern("sdls_counter"), pmt.from_uint64(sdls_counter))
            response_meta = pmt.dict_add(response_meta, pmt.intern("vcid_counter"), pmt.from_long(vcid_counter))

            # Echo inject_db's request id so it can pair this response with its request.
            request_id = pmt.dict_ref(meta, pmt.intern("db_request_id"), pmt.PMT_NIL)
            if not pmt.eqv(request_id, pmt.PMT_NIL):
                response_meta = pmt.dict_add(response_meta, pmt.intern("db_request_id"), request_id)

            in_body = pmt.cdr(msg)
            body_to_forward = pmt.PMT_NIL
            if self.forward_body and pmt.is_u8vector(in_body):
                body_to_forward = in_body
            self._publish("db_callback", response_meta, body_to_forward)

            # Increase counters only after giving out current values.
            # sdls_counter: try to increment; if at max and auto-reset enabled for dummy mode,
            # reset to 0 instead of raising an error. Otherwise mark the entry exhausted so
            # the max value is never served twice.
            try:
                entry["sdls_counter"] = self._checked_increment(sdls_counter, SDLS_COUNTER_MAX, "sdls_counter")
            except OverflowError as exc:
                if self.type == 0 and self.auto_reset_counters:
                    entry["sdls_counter"] = 0
                else:
                    entry["sdls_counter_exhausted"] = True
                    self.logger.error(f"Counter increment aborted: {exc}; further requests for this entry are refused.")

            # vcid_counter is the TC frame sequence number N(S), counted
            # modulo 256 (CCSDS 232.0-B), so it always wraps 255 -> 0.
            entry["vcid_counter"] = (vcid_counter + 1) % (VCID_COUNTER_MAX + 1)
        except Exception as exc:
            self.logger.error(f"Failed to build or publish db_callback: {exc}")




