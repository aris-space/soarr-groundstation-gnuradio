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
        return int(pmt.to_long(pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)))

    def _build_query(self, vcid):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("vcid"), pmt.from_long(vcid))
        return pmt.cons(meta, pmt.PMT_NIL)

    def test_instance(self):
        instance = dbClient()
        self.assertIsNotNone(instance)

    def test_001_tc_query_returns_sequence_and_increments_counter(self):
        block = dbClient(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            block.make_tc_call(self._build_query(0x12))
            block.make_tc_call(self._build_query(0x12))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)

        out_port_1, out_msg_1 = published[0]
        self.assertTrue(pmt.eqv(out_port_1, pmt.intern("tc_callback")))
        out_meta_1 = pmt.car(out_msg_1)
        self.assertEqual(self._meta_int(out_meta_1, "vcid"), 0x12)
        self.assertEqual(self._meta_int(out_meta_1, "scid"), 0x155)
        self.assertEqual(self._meta_int(out_meta_1, "spi"), 1)
        self.assertEqual(self._meta_int(out_meta_1, "frame_sequence_number"), 0)
        self.assertEqual(self._meta_int(out_meta_1, "vcid_counter"), 0)

        out_port_2, out_msg_2 = published[1]
        self.assertTrue(pmt.eqv(out_port_2, pmt.intern("tc_callback")))
        out_meta_2 = pmt.car(out_msg_2)
        self.assertEqual(self._meta_int(out_meta_2, "frame_sequence_number"), 1)
        self.assertEqual(self._meta_int(out_meta_2, "vcid_counter"), 1)

    def test_002_sdls_query_returns_material_and_increments_counter(self):
        block = dbClient(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            block.make_sdls_call(self._build_query(0x12))
            block.make_sdls_call(self._build_query(0x12))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)

        out_port_1, out_msg_1 = published[0]
        self.assertTrue(pmt.eqv(out_port_1, pmt.intern("sdls_callback")))
        out_meta_1 = pmt.car(out_msg_1)
        self.assertEqual(self._meta_int(out_meta_1, "vcid"), 0x12)
        self.assertEqual(self._meta_int(out_meta_1, "scid"), 0x155)
        self.assertEqual(self._meta_int(out_meta_1, "spi"), 1)
        self.assertEqual(self._meta_int(out_meta_1, "sdls_counter"), 0)

        enc_key = pmt.symbol_to_string(pmt.dict_ref(out_meta_1, pmt.intern("encryption_key"), pmt.PMT_NIL))
        auth_key = pmt.symbol_to_string(pmt.dict_ref(out_meta_1, pmt.intern("authentication_key"), pmt.PMT_NIL))
        self.assertEqual(enc_key, "00112233445566778899AABBCCDDEEFF")
        self.assertEqual(auth_key, "FFEEDDCCBBAA99887766554433221100")

        out_port_2, out_msg_2 = published[1]
        self.assertTrue(pmt.eqv(out_port_2, pmt.intern("sdls_callback")))
        out_meta_2 = pmt.car(out_msg_2)
        self.assertEqual(self._meta_int(out_meta_2, "sdls_counter"), 1)

    def test_003_missing_vcid_emits_no_response(self):
        block = dbClient(type=0)
        original_pub, published = self._capture_pub(block)

        try:
            msg = pmt.cons(pmt.make_dict(), pmt.PMT_NIL)
            block.make_tc_call(msg)
            block.make_sdls_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_004_yaml_mode_loads_entries_and_uses_vcid_data(self):
        yaml_content = """
entries:
  \"5\":
    SCID: 341
    SPI: 7
    VCID: 5
    encryption_key: \"AAAA\"
    authentication_key: \"BBBB\"
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
                block.make_tc_call(self._build_query(5))
                block.make_sdls_call(self._build_query(5))
            finally:
                self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)

        tc_port, tc_msg = published[0]
        self.assertTrue(pmt.eqv(tc_port, pmt.intern("tc_callback")))
        tc_meta = pmt.car(tc_msg)
        self.assertEqual(self._meta_int(tc_meta, "vcid"), 5)
        self.assertEqual(self._meta_int(tc_meta, "scid"), 341)
        self.assertEqual(self._meta_int(tc_meta, "spi"), 7)
        self.assertEqual(self._meta_int(tc_meta, "frame_sequence_number"), 34)

        sdls_port, sdls_msg = published[1]
        self.assertTrue(pmt.eqv(sdls_port, pmt.intern("sdls_callback")))
        sdls_meta = pmt.car(sdls_msg)
        self.assertEqual(self._meta_int(sdls_meta, "sdls_counter"), 12)
        self.assertEqual(
            pmt.symbol_to_string(pmt.dict_ref(sdls_meta, pmt.intern("encryption_key"), pmt.PMT_NIL)),
            "AAAA",
        )


if __name__ == '__main__':
    gr_unittest.run(qa_dbClient)
