#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
import logging
from pathlib import Path

from gnuradio import gr
import pmt

try:
    import yaml
except ImportError:
    yaml = None

class dbClient(gr.basic_block):
    """Database client for TC counter and SDLS material lookup.

    Modes:
    - type=0: in-memory dummy database with fixed defaults (fast testing)
    - type=1: local YAML-backed database
    - type=2: remote real DB (stub, not implemented yet)
    """

    def __init__(self, type:int=0x0, ip:int=0x0, port:int=80, yaml_path:str=""):
        gr.basic_block.__init__(self,
                name="dbClient",
                in_sig=None,
            out_sig=None)

        self.logger = logging.getLogger("gnuradio.sage.dbClient")

        # 0=dummy, 1=local yaml, 2=remote real DB (stub)
        self.type = int(type)

        # Reserved for remote DB mode.
        self.ip = ip
        self.port = port

        # Optional path for local YAML mode.
        self.yaml_path = str(yaml_path) if yaml_path else ""

        # In-memory structure indexed by VCID string key.
        self._db = {}
        self._init_database()

        # Dedicated ports.
        self.message_port_register_in(pmt.intern("tc_query"))
        self.message_port_register_in(pmt.intern("sdls_query"))
        self.message_port_register_out(pmt.intern("tc_callback"))
        self.message_port_register_out(pmt.intern("sdls_callback"))

        self.set_msg_handler(pmt.intern("tc_query"), self.make_tc_call)
        self.set_msg_handler(pmt.intern("sdls_query"), self.make_sdls_call)

    def _init_database(self):
        if self.type == 0:
            self._db = self._dummy_db()
            return

        if self.type == 1:
            self._db = self._load_yaml_db()
            return

        # type=2 not implemented yet; keep empty db and log.
        if self.type == 2:
            self.logger.warning("Remote DB mode (type=2) is not implemented yet. Returning fallback data.")
            self._db = self._dummy_db()
            return

        self.logger.warning("Unknown db type '%s'. Falling back to dummy mode.", self.type)
        self._db = self._dummy_db()

    def _dummy_db(self):
        # Default single VCID entry for rapid integration testing.
        return {
            "18": {
                "SCID": 0x155,
                "SPI": 1,
                "VCID": 0x12,
                "encryption_key": "00112233445566778899AABBCCDDEEFF",
                "authentication_key": "FFEEDDCCBBAA99887766554433221100",
                "sdls_counter": 0,
                "vcid_counter": 0,
                "key_state": {
                    "enc": "active",
                    "auth": "active",
                },
            }
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
            self.logger.error("YAML database file not found: %s. Falling back to dummy DB.", path)
            return self._dummy_db()

        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        except Exception as exc:
            self.logger.error("Failed to parse YAML database '%s': %s. Falling back to dummy DB.", path, exc)
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
        for key, item in entries.items():
            if not isinstance(item, dict):
                continue
            vcid_val = self._to_int(item.get("VCID", key), default=0)
            normalized[str(vcid_val)] = {
                "SCID": self._to_int(item.get("SCID"), default=0),
                "SPI": self._to_int(item.get("SPI"), default=0),
                "VCID": vcid_val,
                "encryption_key": str(item.get("encryption_key", "")),
                "authentication_key": str(item.get("authentication_key", "")),
                "sdls_counter": self._to_int(item.get("sdls_counter"), default=0),
                "vcid_counter": self._to_int(item.get("vcid_counter"), default=0),
                "key_state": item.get("key_state", {}),
            }

        if not normalized:
            self.logger.error("No valid entries found in YAML database. Falling back to dummy DB.")
            return self._dummy_db()

        return normalized

    def _to_int(self, value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    def _pmt_dict_get_int(self, meta, key, default=None):
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

    def _entry_from_meta(self, meta):
        vcid = self._pmt_dict_get_int(meta, "vcid", None)
        if vcid is None:
            return None
        return self._db.get(str(vcid))

    def _publish(self, port_name, response_meta):
        out_msg = pmt.cons(response_meta, pmt.PMT_NIL)
        self.message_port_pub(pmt.intern(port_name), out_msg)

    def make_tc_call(self, msg):
        """Handle TC counter lookup/update requests (VCID counter path)."""
        meta = pmt.car(msg)
        if not pmt.is_dict(meta):
            self.logger.error("TC query metadata is not a dictionary.")
            return

        entry = self._entry_from_meta(meta)
        if entry is None:
            self.logger.error("TC query missing or unknown VCID.")
            return

        # Return current counter and then increment for next frame allocation.
        frame_seq_num = int(entry.get("vcid_counter", 0)) & 0xFF
        entry["vcid_counter"] = (frame_seq_num + 1) & 0xFF

        response_meta = pmt.make_dict()
        response_meta = pmt.dict_add(response_meta, pmt.intern("vcid"), pmt.from_long(int(entry.get("VCID", 0))))
        response_meta = pmt.dict_add(response_meta, pmt.intern("scid"), pmt.from_long(int(entry.get("SCID", 0))))
        response_meta = pmt.dict_add(response_meta, pmt.intern("spi"), pmt.from_long(int(entry.get("SPI", 0))))
        response_meta = pmt.dict_add(
            response_meta, pmt.intern("frame_sequence_number"), pmt.from_long(frame_seq_num)
        )
        response_meta = pmt.dict_add(response_meta, pmt.intern("vcid_counter"), pmt.from_long(frame_seq_num))

        self._publish("tc_callback", response_meta)

    def make_sdls_call(self, msg):
        """Handle SDLS context requests (keys, key state, SDLS counter)."""
        meta = pmt.car(msg)
        if not pmt.is_dict(meta):
            self.logger.error("SDLS query metadata is not a dictionary.")
            return

        entry = self._entry_from_meta(meta)
        if entry is None:
            self.logger.error("SDLS query missing or unknown VCID.")
            return

        sdls_counter = int(entry.get("sdls_counter", 0)) & 0xFFFFFFFF

        response_meta = pmt.make_dict()
        response_meta = pmt.dict_add(response_meta, pmt.intern("vcid"), pmt.from_long(int(entry.get("VCID", 0))))
        response_meta = pmt.dict_add(response_meta, pmt.intern("scid"), pmt.from_long(int(entry.get("SCID", 0))))
        response_meta = pmt.dict_add(response_meta, pmt.intern("spi"), pmt.from_long(int(entry.get("SPI", 0))))
        response_meta = pmt.dict_add(response_meta, pmt.intern("sdls_counter"), pmt.from_long(sdls_counter))
        response_meta = pmt.dict_add(
            response_meta,
            pmt.intern("encryption_key"),
            pmt.intern(str(entry.get("encryption_key", ""))),
        )
        response_meta = pmt.dict_add(
            response_meta,
            pmt.intern("authentication_key"),
            pmt.intern(str(entry.get("authentication_key", ""))),
        )
        response_meta = pmt.dict_add(
            response_meta,
            pmt.intern("key_state"),
            pmt.intern(str(entry.get("key_state", {}))),
        )

        # Bump counter after handing out current value.
        entry["sdls_counter"] = (sdls_counter + 1) & 0xFFFFFFFF

        self._publish("sdls_callback", response_meta)


