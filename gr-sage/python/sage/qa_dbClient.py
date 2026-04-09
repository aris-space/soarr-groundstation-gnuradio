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

from gnuradio.sage import dbClient

class qa_dbClient(gr_unittest.TestCase):

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

    def _build_query(self, scid, spi):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(scid))
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(spi))
        return pmt.cons(meta, pmt.PMT_NIL)

    def test_instance(self):
        instance = dbClient()
        self.assertIsNotNone(instance)

    def test_001_db_call_returns_fields_and_increments_both_counters(self):
        block = dbClient(type=0)
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
        block = dbClient(type=0)
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
        block = dbClient(type=0)
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

            block = dbClient(type=1, yaml_path=str(yaml_path))
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

    def test_005_counter_max_no_wraparound(self):
        block = dbClient(type=0)
        # Force counters to maximum values.
        block._db["18"]["sdls_counter"] = 0xFFFFFFFF
        block._db["18"]["vcid_counter"] = 0xFF

        original_pub, published = self._capture_pub(block)
        try:
            block.make_db_call(self._build_query(0x155, 1))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_callback")))
        out_meta = pmt.car(out_msg)
        self.assertEqual(self._meta_int(out_meta, "sdls_counter"), 0xFFFFFFFF)
        self.assertEqual(self._meta_int(out_meta, "vcid_counter"), 0xFF)

        # Ensure no wrap-around happened after callback publication.
        self.assertEqual(block._db["18"]["sdls_counter"], 0xFFFFFFFF)
        self.assertEqual(block._db["18"]["vcid_counter"], 0xFF)


if __name__ == '__main__':
    gr_unittest.run(qa_dbClient)
