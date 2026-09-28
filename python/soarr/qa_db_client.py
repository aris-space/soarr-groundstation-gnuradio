#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import tempfile
from pathlib import Path

from gnuradio import gr, gr_unittest
import pmt

from gnuradio.soarr import db_client

class qa_db_client(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def _capture_pub(self, block):
        original_pub = block.message_port_pub
        published = []

        def _capture(port, msg):
            published.append((port, msg))

        block.message_port_pub = _capture
        return original_pub, published

    def _restore_pub(self, block, original_pub):
        block.message_port_pub = original_pub

    def _meta_int(self, meta, key):
        value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
        if pmt.is_uint64(value):
            return int(pmt.to_uint64(value))
        return int(pmt.to_long(value))

    def _build_query(self, scid, spi, body=None):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(scid))
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(spi))
        payload = body if body is not None else pmt.PMT_NIL
        return pmt.cons(meta, payload)

    def test_instance(self):
        instance = db_client()
        self.assertIsNotNone(instance)

    def test_001_db_call_returns_fields_and_increments_both_counters(self):
        block = db_client(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            block.make_db_call(self._build_query(0x155, 1))
            block.make_db_call(self._build_query(0x155, 1))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)

        out_port_1, out_msg_1 = published[0]
        self.assertTrue(pmt.eqv(out_port_1, pmt.intern("db_callback")))
        out_meta_1 = pmt.car(out_msg_1)
        self.assertEqual(self._meta_int(out_meta_1, "scid"), 0x155)
        self.assertEqual(self._meta_int(out_meta_1, "spi"), 1)
        self.assertEqual(self._meta_int(out_meta_1, "vcid"), 0x12)
        self.assertEqual(self._meta_int(out_meta_1, "vcid_counter"), 0)
        self.assertEqual(self._meta_int(out_meta_1, "sdls_counter"), 0)

        crypt_key_1 = pmt.symbol_to_string(pmt.dict_ref(out_meta_1, pmt.intern("crypt_key"), pmt.PMT_NIL))
        auth_key_1 = pmt.symbol_to_string(pmt.dict_ref(out_meta_1, pmt.intern("auth_key"), pmt.PMT_NIL))
        self.assertEqual(crypt_key_1, "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF")
        self.assertEqual(auth_key_1, "FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100")

        out_port_2, out_msg_2 = published[1]
        self.assertTrue(pmt.eqv(out_port_2, pmt.intern("db_callback")))
        out_meta_2 = pmt.car(out_msg_2)
        self.assertEqual(self._meta_int(out_meta_2, "vcid_counter"), 1)
        self.assertEqual(self._meta_int(out_meta_2, "sdls_counter"), 1)

    def test_002_missing_scid_or_spi_emits_no_response(self):
        block = db_client(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            # Missing spi
            meta_a = pmt.make_dict()
            meta_a = pmt.dict_add(meta_a, pmt.intern("scid"), pmt.from_long(0x155))
            block.make_db_call(pmt.cons(meta_a, pmt.PMT_NIL))

            # Missing scid
            meta_b = pmt.make_dict()
            meta_b = pmt.dict_add(meta_b, pmt.intern("spi"), pmt.from_long(1))
            block.make_db_call(pmt.cons(meta_b, pmt.PMT_NIL))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_003_unknown_scid_spi_emits_no_response(self):
        block = db_client(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            block.make_db_call(self._build_query(0x999, 42))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_004_yaml_mode_loads_entries_and_uses_scid_spi(self):
        yaml_content = """
entries:
  \"5\":
    SCID: 341
    SPI: 7
    VCID: 5
    crypt_key: "AAAA"
    auth_key: "BBBB"
    sdls_counter: 12
    vcid_counter: 34
    key_state:
      enc: active
      auth: standby
"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            block = db_client(type=1, yaml_path=str(yaml_path))
            original_pub, published = self._capture_pub(block)
            try:
                block.make_db_call(self._build_query(341, 7))
            finally:
                self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)

        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_callback")))
        out_meta = pmt.car(out_msg)
        self.assertEqual(self._meta_int(out_meta, "vcid"), 5)
        self.assertEqual(self._meta_int(out_meta, "vcid_counter"), 34)
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), 12)
        self.assertEqual(
            pmt.symbol_to_string(pmt.dict_ref(out_meta, pmt.intern("crypt_key"), pmt.PMT_NIL)),
            "AAAA",
        )

    def test_005_counter_max_served_once_then_refused(self):
        block = db_client(type=0)

        VCID = 0x155
        SPI = 1

        # sdls_counter is the 2-byte IV on the wire (sdls_header).
        SDLS_COUNTER_MAX = 0xFFFF
        VCID_COUNTER_MAX = 0xFF

        # Force counters to maximum values.
        block._db[str(VCID)][str(SPI)]["sdls_counter"] = SDLS_COUNTER_MAX
        block._db[str(VCID)][str(SPI)]["vcid_counter"] = VCID_COUNTER_MAX

        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(self._build_query(VCID, SPI))
            block.make_db_call(self._build_query(VCID, SPI))
        finally:
            self._restore_pub(block, original_pub)

        # The max value is served exactly once; the second request is
        # refused so the same (key, sdls_counter) pair is never reused.
        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_callback")))
        out_meta = pmt.car(out_msg)

        # Verify counters are at max values in the callback metadata.
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), SDLS_COUNTER_MAX)
        self.assertEqual(self._meta_int(out_meta, "vcid_counter"), VCID_COUNTER_MAX)

        # sdls_counter never wraps (that would reuse an AES-CTR counter);
        # vcid_counter, the frame sequence number, wraps modulo 256.
        self.assertEqual(block._db[str(VCID)][str(SPI)]["sdls_counter"], SDLS_COUNTER_MAX)
        self.assertEqual(block._db[str(VCID)][str(SPI)]["vcid_counter"], 0)

    def test_006_dummy_mode_configurable_scid_spi_vcid(self):
        """Test Dummy mode with custom SCID, SPI, VCID parameters."""
        custom_scid = int(0x200)
        custom_spi = int(5)
        custom_vcid = int(0x42)
        
        block = db_client(
            type=0,
            scid=custom_scid,
            spi=custom_spi,
            vcid=custom_vcid,
        )
        original_pub, published = self._capture_pub(block)
        
        try:
            block.make_db_call(self._build_query(custom_scid, custom_spi))
        finally:
            self._restore_pub(block, original_pub)
        
        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_callback")))
        out_meta = pmt.car(out_msg)
        self.assertEqual(self._meta_int(out_meta, "vcid"), custom_vcid)

    def test_007_dummy_mode_configurable_keys(self):
        """Test Dummy mode with custom crypt_key and auth_key."""
        custom_crypt = "DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF"
        custom_auth = "CAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABE"
        
        block = db_client(
            type=0,
            scid=0x155,
            spi=1,
            crypt_key=custom_crypt,
            auth_key=custom_auth,
        )
        original_pub, published = self._capture_pub(block)
        
        try:
            block.make_db_call(self._build_query(0x155, 1))
        finally:
            self._restore_pub(block, original_pub)
        
        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        out_meta = pmt.car(out_msg)
        
        returned_crypt = pmt.symbol_to_string(pmt.dict_ref(out_meta, pmt.intern("crypt_key"), pmt.PMT_NIL))
        returned_auth = pmt.symbol_to_string(pmt.dict_ref(out_meta, pmt.intern("auth_key"), pmt.PMT_NIL))
        
        self.assertEqual(returned_crypt, custom_crypt)
        self.assertEqual(returned_auth, custom_auth)

    def test_008_dummy_mode_configurable_counters(self):
        """Test Dummy mode with custom initial counter values."""
        custom_sdls_counter = 123
        custom_vcid_counter = 45
        
        block = db_client(
            type=0,
            scid=0x155,
            spi=1,
            sdls_counter=custom_sdls_counter,
            vcid_counter=custom_vcid_counter,
        )
        original_pub, published = self._capture_pub(block)
        
        try:
            block.make_db_call(self._build_query(0x155, 1))
            block.make_db_call(self._build_query(0x155, 1))
        finally:
            self._restore_pub(block, original_pub)
        
        self.assertEqual(len(published), 2)
        
        # First call should return initial values
        out_meta_1 = pmt.car(published[0][1])
        self.assertEqual(self._meta_int(out_meta_1, "sdls_counter"), custom_sdls_counter)
        self.assertEqual(self._meta_int(out_meta_1, "vcid_counter"), custom_vcid_counter)
        
        # Second call should return incremented values
        out_meta_2 = pmt.car(published[1][1])
        self.assertEqual(self._meta_int(out_meta_2, "sdls_counter"), custom_sdls_counter + 1)
        self.assertEqual(self._meta_int(out_meta_2, "vcid_counter"), custom_vcid_counter + 1)

    def test_009_dummy_mode_configurable_key_states(self):
        """Test Dummy mode with custom key state enc and auth."""
        custom_enc = "standby"
        custom_auth = "inactive"
        custom_scid = int(0x155)
        custom_spi = int(1)
        
        block = db_client(
            type=0,
            scid=custom_scid,
            spi=custom_spi,
            key_state_enc=custom_enc,
            key_state_auth=custom_auth,
        )
        
        # Verify key states are stored in the DB
        entry = block._db[str(custom_scid)][str(custom_spi)]
        self.assertEqual(entry["key_state"]["enc"], custom_enc)
        self.assertEqual(entry["key_state"]["auth"], custom_auth)

    def test_010_dummy_mode_all_custom_parameters(self):
        """Test Dummy mode with all custom parameters set simultaneously."""
        custom_scid = int(0x333)
        custom_spi = int(9)
        custom_vcid = int(0x77)
        custom_crypt = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        custom_auth = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        custom_sdls = 999
        custom_vcid_counter = 88
        
        params = {
            "type": 0,
            "scid": custom_scid,
            "spi": custom_spi,
            "vcid": custom_vcid,
            "crypt_key": custom_crypt,
            "auth_key": custom_auth,
            "sdls_counter": custom_sdls,
            "vcid_counter": custom_vcid_counter,
            "key_state_enc": "standby",
            "key_state_auth": "active",
        }
        
        block = db_client(**params)
        original_pub, published = self._capture_pub(block)
        
        try:
            block.make_db_call(self._build_query(custom_scid, custom_spi))
        finally:
            self._restore_pub(block, original_pub)
        
        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        out_meta = pmt.car(out_msg)
        
        self.assertEqual(self._meta_int(out_meta, "vcid"), custom_vcid)
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), custom_sdls)
        self.assertEqual(self._meta_int(out_meta, "vcid_counter"), custom_vcid_counter)
        
        returned_crypt = pmt.symbol_to_string(pmt.dict_ref(out_meta, pmt.intern("crypt_key"), pmt.PMT_NIL))
        returned_auth = pmt.symbol_to_string(pmt.dict_ref(out_meta, pmt.intern("auth_key"), pmt.PMT_NIL))
        
        self.assertEqual(returned_crypt, custom_crypt)
        self.assertEqual(returned_auth, custom_auth)

    def test_011_forward_body_enabled_preserves_u8vector(self):
        block = db_client(type=0, forward_body=True)
        original_pub, published = self._capture_pub(block)

        body = pmt.init_u8vector(4, [1, 2, 3, 4])
        try:
            block.make_db_call(self._build_query(0x155, 1, body=body))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_msg = published[0][1]
        out_body = pmt.cdr(out_msg)
        self.assertTrue(pmt.is_u8vector(out_body))
        self.assertEqual(list(pmt.u8vector_elements(out_body)), [1, 2, 3, 4])

    def test_012_forward_body_disabled_drops_payload(self):
        block = db_client(type=0, forward_body=False)
        original_pub, published = self._capture_pub(block)

        body = pmt.init_u8vector(4, [1, 2, 3, 4])
        try:
            block.make_db_call(self._build_query(0x155, 1, body=body))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_msg = published[0][1]
        out_body = pmt.cdr(out_msg)
        self.assertTrue(pmt.eqv(out_body, pmt.PMT_NIL))

    def test_013_auto_reset_enabled_resets_counters(self):
        block = db_client(type=0, auto_reset_counters=True)

        VCID = 0x155
        SPI = 1

        SDLS_COUNTER_MAX = 0xFFFF
        VCID_COUNTER_MAX = 0xFF

        # Force counters to maximum values.
        block._db[str(VCID)][str(SPI)]["sdls_counter"] = SDLS_COUNTER_MAX
        block._db[str(VCID)][str(SPI)]["vcid_counter"] = VCID_COUNTER_MAX

        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(self._build_query(VCID, SPI))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)

        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_callback")))
        out_meta = pmt.car(out_msg)

        # Verify counters are at max values in the callback metadata.
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), SDLS_COUNTER_MAX)
        self.assertEqual(self._meta_int(out_meta, "vcid_counter"), VCID_COUNTER_MAX)

        # With auto-reset enabled for dummy mode, counters should be reset to 0.
        self.assertEqual(block._db[str(VCID)][str(SPI)]["sdls_counter"], 0)
        self.assertEqual(block._db[str(VCID)][str(SPI)]["vcid_counter"], 0)

    def test_014_auto_reset_disabled_keeps_max_and_refuses(self):
        block = db_client(type=0, auto_reset_counters=False)

        VCID = 0x155
        SPI = 1

        SDLS_COUNTER_MAX = 0xFFFF
        VCID_COUNTER_MAX = 0xFF

        # Force counters to maximum values.
        block._db[str(VCID)][str(SPI)]["sdls_counter"] = SDLS_COUNTER_MAX
        block._db[str(VCID)][str(SPI)]["vcid_counter"] = VCID_COUNTER_MAX

        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(self._build_query(VCID, SPI))
            block.make_db_call(self._build_query(VCID, SPI))
            block.make_db_call(self._build_query(VCID, SPI))
        finally:
            self._restore_pub(block, original_pub)

        # Only the first request is answered; the exhausted entry stays
        # refused for every later request.
        self.assertEqual(len(published), 1)

        # sdls_counter stays at max (entry exhausted); vcid_counter wraps
        # regardless of auto_reset_counters.
        self.assertEqual(block._db[str(VCID)][str(SPI)]["sdls_counter"], SDLS_COUNTER_MAX)
        self.assertEqual(block._db[str(VCID)][str(SPI)]["vcid_counter"], 0)

    # Additional: a non-pair input must not crash the handler - dropped
    # cleanly (logged, no publish) instead of pmt.car raising out of it.
    def test_015_non_pair_input_is_dropped_not_raised(self):
        block = db_client(type=0)
        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(pmt.intern("not-a-pair"))  # must not raise
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    # Additional: scid and spi are resolved independently - a valid
    # top-level scid must not be discarded just because top-level spi is
    # absent (and vice versa). Previously, a present top-level scid was
    # silently replaced by a different nested scid whenever spi alone was
    # missing at the top level.
    def test_016_scid_spi_resolved_independently(self):
        block = db_client(type=0, scid=0x155, spi=1)
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            # Top-level scid present (the real value); top-level spi absent.
            meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x155))
            # Nested tc_header has a different (wrong) scid, to prove it's
            # not used when a valid top-level scid already exists.
            tc_header = pmt.make_dict()
            tc_header = pmt.dict_add(tc_header, pmt.intern("scid"), pmt.from_long(0x999))
            telecommand = pmt.make_dict()
            telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), telecommand)
            # Nested sdls has the real spi.
            sdls_header = pmt.make_dict()
            sdls_header = pmt.dict_add(sdls_header, pmt.intern("spi"), pmt.from_long(1))
            sdls = pmt.make_dict()
            sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
            meta = pmt.dict_add(meta, pmt.intern("sdls"), sdls)

            block.make_db_call(pmt.cons(meta, pmt.PMT_NIL))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_meta = pmt.car(published[0][1])
        self.assertEqual(self._meta_int(out_meta, "scid"), 0x155)
        self.assertEqual(self._meta_int(out_meta, "spi"), 1)

    # Additional: same independence fix for bypass/control.
    def test_017_bypass_control_resolved_independently(self):
        block = db_client(type=0, scid=0x155, spi=1)
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(0x155))
            meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(1))
            # Top-level bypass present (the real value); top-level control absent.
            meta = pmt.dict_add(meta, pmt.intern("bypass"), pmt.from_bool(True))
            # Nested tc_header has a different (wrong) bypass, to prove it's
            # not used when a valid top-level bypass already exists.
            tc_header = pmt.make_dict()
            tc_header = pmt.dict_add(tc_header, pmt.intern("bypass"), pmt.from_bool(False))
            tc_header = pmt.dict_add(tc_header, pmt.intern("control"), pmt.from_bool(True))
            telecommand = pmt.make_dict()
            telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), telecommand)

            block.make_db_call(pmt.cons(meta, pmt.PMT_NIL))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_meta = pmt.car(published[0][1])
        bypass = pmt.to_bool(pmt.dict_ref(out_meta, pmt.intern("bypass"), pmt.PMT_NIL))
        control = pmt.to_bool(pmt.dict_ref(out_meta, pmt.intern("control"), pmt.PMT_NIL))
        self.assertTrue(bypass)
        self.assertTrue(control)

    # Additional: the nested-by-SCID YAML layout (outer key is the SCID,
    # inner keys are SPIs, neither entry carries an explicit SCID/SPI
    # field) is a real accepted layout distinct from the flat one.
    def test_018_yaml_mode_nested_by_scid_layout(self):
        yaml_content = """
"341":
  "7":
    VCID: 5
    crypt_key: "AAAA"
    auth_key: "BBBB"
    sdls_counter: 12
    vcid_counter: 34
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            block = db_client(type=1, yaml_path=str(yaml_path))
            original_pub, published = self._capture_pub(block)
            try:
                block.make_db_call(self._build_query(341, 7))
            finally:
                self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_meta = pmt.car(published[0][1])
        self.assertEqual(self._meta_int(out_meta, "vcid"), 5)
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), 12)

    # Additional: an internal failure past field extraction (e.g. publish
    # itself raising) is caught, logged, and dropped - not left to raise
    # out of the real message handler.
    def test_019_internal_publish_failure_is_dropped_not_raised(self):
        block = db_client(type=0, scid=0x155, spi=1)
        original_pub, published = self._capture_pub(block)
        try:
            def _raise(port, out_msg):
                raise RuntimeError("simulated publish failure")

            block.message_port_pub = _raise
            block.make_db_call(self._build_query(0x155, 1))  # must not raise
        finally:
            self._restore_pub(block, original_pub)

    # sdls_counter is carried on the wire as the 2-byte SDLS IV, so a
    # stored value above 16 bits can never be transmitted and is refused.
    def test_020_sdls_counter_above_16_bits_is_refused(self):
        yaml_content = """
entries:
  "a":
    SCID: 341
    SPI: 1
    sdls_counter: 65536
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            block = db_client(type=1, yaml_path=str(yaml_path))
            original_pub, published = self._capture_pub(block)
            try:
                block.make_db_call(self._build_query(341, 1))
            finally:
                self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    # Exhausting one SCID/SPI entry's sdls_counter refuses only that
    # entry; other entries keep being served.
    def test_021_exhausted_entry_does_not_block_other_entries(self):
        yaml_content = """
entries:
  "a":
    SCID: 341
    SPI: 1
    sdls_counter: 65535
  "b":
    SCID: 341
    SPI: 2
    sdls_counter: 7
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            block = db_client(type=1, yaml_path=str(yaml_path))
            original_pub, published = self._capture_pub(block)
            try:
                block.make_db_call(self._build_query(341, 1))  # serves 65535
                block.make_db_call(self._build_query(341, 1))  # refused
                block.make_db_call(self._build_query(341, 2))  # served
            finally:
                self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)
        self.assertEqual(self._meta_int(pmt.car(published[0][1]), "sdls_counter"), 65535)
        self.assertEqual(self._meta_int(pmt.car(published[1][1]), "spi"), 2)
        self.assertEqual(self._meta_int(pmt.car(published[1][1]), "sdls_counter"), 7)

    # inject_db tags each query with db_request_id to pair responses with
    # requests; the response must carry the same value back.
    def test_022_echoes_db_request_id(self):
        block = db_client(type=0, scid=0x155, spi=1)
        query = self._build_query(0x155, 1)
        query = pmt.cons(
            pmt.dict_add(pmt.car(query), pmt.intern("db_request_id"), pmt.from_uint64(42)),
            pmt.cdr(query),
        )
        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(query)
            block.make_db_call(self._build_query(0x155, 1))  # no id: none echoed
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)
        self.assertEqual(self._meta_int(pmt.car(published[0][1]), "db_request_id"), 42)
        self.assertFalse(pmt.dict_has_key(pmt.car(published[1][1]), pmt.intern("db_request_id")))

    # vcid_counter becomes the TC frame sequence number N(S), which CCSDS
    # 232.0-B counts modulo 256: 255 is followed by 0, in every DB mode.
    def test_023_vcid_counter_wraps_modulo_256(self):
        yaml_content = """
entries:
  "a":
    SCID: 341
    SPI: 1
    vcid_counter: 254
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            block = db_client(type=1, yaml_path=str(yaml_path))
            original_pub, published = self._capture_pub(block)
            try:
                for _ in range(4):
                    block.make_db_call(self._build_query(341, 1))
            finally:
                self._restore_pub(block, original_pub)

        served = [self._meta_int(pmt.car(msg), "vcid_counter") for _, msg in published]
        self.assertEqual(served, [254, 255, 0, 1])


if __name__ == '__main__':
    gr_unittest.run(qa_db_client)
