#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from gnuradio.sage import Injectdb
import pmt

class qa_Injectdb(gr_unittest.TestCase):

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

    def _mk_meta(self, kv):
        meta = pmt.make_dict()
        for k, v in kv.items():
            if isinstance(v, int):
                pmt_v = pmt.from_long(v)
            else:
                pmt_v = v
            meta = pmt.dict_add(meta, pmt.intern(k), pmt_v)
        return meta

    def _mk_pdu(self, kv, payload=None):
        meta = self._mk_meta(kv)
        payload = payload if payload is not None else [1, 2, 3]
        u8 = pmt.init_u8vector(len(payload), payload)
        return pmt.cons(meta, u8)

    def test_instance(self):
        instance = Injectdb()
        self.assertIsNotNone(instance)

    def test_001_in_valid_pdu_emits_db_call(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({"scid": 0x155, "spi": 1})
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_call")))
        self.assertTrue(pmt.equal(out_msg, msg))

    def test_002_in_missing_required_key_emits_nothing(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({"scid": 0x155})
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_003_in_non_integer_scid_or_spi_emits_nothing(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({"scid": pmt.PMT_NIL, "spi": 1})
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_004_db_callback_valid_integer_material_emits_out(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({
                "scid": 0x155,
                "spi": 1,
                "auth_key": 11,
                "crypt_key": 22,
                "vcid": 0x12,
                "vcid_counter": 1,
                "sdls_counter": 2,
            })
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.equal(out_msg, msg))

    def test_005_db_callback_allows_nil_auth_and_crypt(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({
                "scid": 0x155,
                "spi": 1,
                "auth_key": pmt.PMT_NIL,
                "crypt_key": pmt.PMT_NIL,
                "vcid": 0x12,
                "vcid_counter": 1,
                "sdls_counter": 2,
            })
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, _ = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

    def test_006_db_callback_rejects_wrong_material_type(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({
                "scid": 0x155,
                "spi": 1,
                "auth_key": pmt.intern("AABB"),
                "crypt_key": 22,
                "vcid": 0x12,
                "vcid_counter": 1,
                "sdls_counter": 2,
            })
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_007_db_callback_rejects_non_integer_required_field(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            msg = self._mk_pdu({
                "scid": 0x155,
                "spi": 1,
                "auth_key": pmt.PMT_NIL,
                "crypt_key": pmt.PMT_NIL,
                "vcid": pmt.intern("12"),
                "vcid_counter": 1,
                "sdls_counter": 2,
            })
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_008_rejects_non_u8vector_payload(self):
        block = Injectdb()
        original_pub, published = self._capture_pub(block)
        try:
            meta = self._mk_meta({"scid": 0x155, "spi": 1})
            msg = pmt.cons(meta, pmt.PMT_NIL)
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_Injectdb)
