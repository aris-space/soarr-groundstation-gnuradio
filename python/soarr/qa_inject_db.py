#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from gnuradio.soarr import inject_db
import pmt

class qa_inject_db(gr_unittest.TestCase):

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
            if isinstance(v, bool):
                pmt_v = pmt.from_bool(v)
            elif isinstance(v, int):
                pmt_v = pmt.from_long(v)
            else:
                pmt_v = v
            meta = pmt.dict_add(meta, pmt.intern(k), pmt_v)
        return meta

    def _mk_nested_tc(self, tc_kv):
        tc_header = pmt.make_dict()
        for k, v in tc_kv.items():
            if isinstance(v, bool):
                pmt_v = pmt.from_bool(v)
            elif isinstance(v, int):
                pmt_v = pmt.from_long(v)
            else:
                pmt_v = v
            tc_header = pmt.dict_add(tc_header, pmt.intern(k), pmt_v)
        telecommand = pmt.make_dict()
        telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
        return telecommand

    def _mk_nested_sdls(self, sdls_kv):
        sdls_header = pmt.make_dict()
        for k, v in sdls_kv.items():
            if isinstance(v, bool):
                pmt_v = pmt.from_bool(v)
            elif isinstance(v, int):
                pmt_v = pmt.from_long(v)
            else:
                pmt_v = v
            sdls_header = pmt.dict_add(sdls_header, pmt.intern(k), pmt_v)
        sdls = pmt.make_dict()
        sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
        return sdls

    def _mk_pdu(self, kv, payload=None):
        meta = self._mk_meta(kv)
        payload = payload if payload is not None else [1, 2, 3]
        u8 = pmt.init_u8vector(len(payload), payload)
        return pmt.cons(meta, u8)

    def _get_nested_value(self, meta, path):
        current = meta
        for key in path[:-1]:
            current = pmt.dict_ref(current, pmt.intern(key), pmt.PMT_NIL)
            if pmt.eqv(current, pmt.PMT_NIL) or not pmt.is_dict(current):
                return pmt.PMT_NIL
        return pmt.dict_ref(current, pmt.intern(path[-1]), pmt.PMT_NIL)

    def test_instance(self):
        instance = inject_db()
        self.assertIsNotNone(instance)

    def test_001_in_valid_pdu_emits_db_call(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_call")))
        # The query is the input PDU plus a db_request_id for correlating
        # the response; every original key and the payload are unchanged.
        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("db_request_id")))
        self.assertTrue(pmt.equal(pmt.dict_delete(out_meta, pmt.intern("db_request_id")), meta))
        self.assertTrue(pmt.equal(pmt.cdr(out_msg), pmt.cdr(msg)))

    def test_002_in_missing_required_key_emits_nothing(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_003_in_non_integer_scid_or_spi_emits_nothing(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": pmt.PMT_NIL,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_004_db_callback_valid_symbol_material_emits_out(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern("FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"))
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.equal(out_msg, msg))

    def test_005_db_callback_allows_nil_auth_and_crypt(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.PMT_NIL)
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.PMT_NIL)
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.equal(out_msg, msg))

    def test_006_db_callback_allows_symbol_hex_auth_and_crypt(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern("FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"))
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, out_msg = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        self.assertTrue(pmt.equal(out_msg, msg))

    def test_007_db_callback_rejects_wrong_material_type(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.PMT_NIL)
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.init_u8vector(2, [1, 2]))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_008_db_callback_rejects_non_integer_required_field(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": pmt.intern("12"),
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.PMT_NIL)
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.PMT_NIL)
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_009_rejects_non_u8vector_payload(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            msg = pmt.cons(meta, pmt.PMT_NIL)
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_010_in_accepts_integer_bool_flags(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": 0,
                "control_flag": 1,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)
        out_port, _ = published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("db_call")))

    def test_011_send_db_call_missing_spi_emits_nothing(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    def test_012_db_merge_fills_missing_nested_keys(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            in_msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(in_msg)

            db_meta = self._mk_meta({
                "auth_key": pmt.intern("FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"),
                "crypt_key": pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
                "vcid": 0x12,
                "vcid_counter": 7,
                "sdls_counter": 9,
            })
            db_msg = pmt.cons(db_meta, pmt.init_u8vector(3, [9, 9, 9]))
            block.send_msg_out(db_msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)
        out_port, out_msg = published[1]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_meta = pmt.car(out_msg)
        tc_vcid = self._get_nested_value(out_meta, ["telecommand", "tc_header", "vcid"])
        tc_vcid_counter = self._get_nested_value(out_meta, ["telecommand", "tc_header", "vcid_counter"])
        sdls_counter = self._get_nested_value(out_meta, ["sdls", "security_header", "sdls_counter"])
        self.assertTrue(pmt.is_integer(tc_vcid) and int(pmt.to_long(tc_vcid)) == 0x12)
        self.assertTrue(pmt.is_integer(tc_vcid_counter) and int(pmt.to_long(tc_vcid_counter)) == 7)
        self.assertTrue(pmt.is_integer(sdls_counter) and int(pmt.to_long(sdls_counter)) == 9)

    def test_013_db_merge_does_not_overwrite_existing(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x44,
                "vcid_counter": 3,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 5,
            }))
            in_msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(in_msg)

            db_meta = self._mk_meta({
                "vcid": 0x12,
                "vcid_counter": 99,
                "sdls_counter": 77,
                "auth_key": pmt.PMT_NIL,
                "crypt_key": pmt.PMT_NIL,
            })
            db_msg = pmt.cons(db_meta, pmt.init_u8vector(3, [9, 9, 9]))
            block.send_msg_out(db_msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)
        out_port, out_msg = published[1]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_meta = pmt.car(out_msg)
        tc_vcid = self._get_nested_value(out_meta, ["telecommand", "tc_header", "vcid"])
        tc_vcid_counter = self._get_nested_value(out_meta, ["telecommand", "tc_header", "vcid_counter"])
        sdls_counter = self._get_nested_value(out_meta, ["sdls", "security_header", "sdls_counter"])
        self.assertTrue(pmt.is_integer(tc_vcid) and int(pmt.to_long(tc_vcid)) == 0x44)
        self.assertTrue(pmt.is_integer(tc_vcid_counter) and int(pmt.to_long(tc_vcid_counter)) == 3)
        self.assertTrue(pmt.is_integer(sdls_counter) and int(pmt.to_long(sdls_counter)) == 5)

        # A genuinely nil auth_key/crypt_key must survive the merge as
        # PMT_NIL, not get corrupted into some other PMT value.
        auth_key = pmt.dict_ref(out_meta, pmt.intern("auth_key"), pmt.intern("MISSING"))
        crypt_key = pmt.dict_ref(out_meta, pmt.intern("crypt_key"), pmt.intern("MISSING"))
        self.assertTrue(pmt.eqv(auth_key, pmt.PMT_NIL))
        self.assertTrue(pmt.eqv(crypt_key, pmt.PMT_NIL))

    # Additional: auth_key/crypt_key genuinely absent (not even set to
    # PMT_NIL) must also be accepted - secret_or_nil means absent-or-nil.
    def test_014_db_callback_allows_absent_auth_and_crypt(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            # auth_key/crypt_key intentionally not set at all.
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_msg_out(msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 1)

    # Additional: an internal failure past field extraction (e.g. publish
    # itself raising) is caught, logged, and dropped - not left to raise
    # out of the real message handler.
    def test_015_send_db_call_internal_failure_is_dropped_not_raised(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))

            def _raise(port, out_msg):
                raise RuntimeError("simulated publish failure")

            block.message_port_pub = _raise
            block.send_db_call(msg)  # must not raise
        finally:
            self._restore_pub(block, original_pub)

    def test_016_send_msg_out_internal_failure_is_dropped_not_raised(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "vcid": 0x12,
                "vcid_counter": 1,
                "bypass_flag": False,
                "control_flag": True,
            }))
            meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({
                "spi": 1,
                "sdls_counter": 2,
            }))
            meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.PMT_NIL)
            meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.PMT_NIL)
            msg = pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))

            def _raise(port, out_msg):
                raise RuntimeError("simulated publish failure")

            block.message_port_pub = _raise
            block.send_msg_out(msg)  # must not raise
        finally:
            self._restore_pub(block, original_pub)

    def test_017_db_callback_nil_payload_uses_pending_payload(self):
        # Simulates db_client(forward_body=False): the db_callback's own
        # payload is PMT_NIL, but a real send_db_call already stored the
        # original payload as pending state, so the response should still
        # be published using that pending payload, not dropped.
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            in_meta = pmt.make_dict()
            in_meta = pmt.dict_add(in_meta, pmt.intern("telecommand"), self._mk_nested_tc({
                "scid": 0x155,
                "bypass_flag": False,
                "control_flag": True,
            }))
            in_meta = pmt.dict_add(in_meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": 1}))
            in_msg = pmt.cons(in_meta, pmt.init_u8vector(3, [1, 2, 3]))
            block.send_db_call(in_msg)

            db_meta = self._mk_meta({
                "auth_key": pmt.intern("FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"),
                "crypt_key": pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
                "vcid": 0x12,
                "vcid_counter": 7,
                "sdls_counter": 9,
            })
            db_msg = pmt.cons(db_meta, pmt.PMT_NIL)
            block.send_msg_out(db_msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 2)
        out_port, out_msg = published[1]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, bytes([1, 2, 3]))

    def test_018_db_callback_nil_payload_with_no_pending_request_emits_nothing(self):
        # A db_callback with a non-u8vector payload and no preceding
        # send_db_call has no pending payload to fall back on, so it must
        # still be rejected rather than publishing a PDU with a PMT_NIL
        # payload.
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            db_meta = self._mk_meta({
                "auth_key": pmt.intern("FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"),
                "crypt_key": pmt.intern("00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"),
                "vcid": 0x12,
                "vcid_counter": 7,
                "sdls_counter": 9,
            })
            db_msg = pmt.cons(db_meta, pmt.PMT_NIL)
            block.send_msg_out(db_msg)
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(published), 0)

    # --- Request/response correlation (db_request_id) ---

    AUTH_KEY = "FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"
    CRYPT_KEY = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"

    def _mk_request(self, spi, payload):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("telecommand"), self._mk_nested_tc({
            "scid": 0x155,
            "bypass_flag": False,
            "control_flag": True,
        }))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), self._mk_nested_sdls({"spi": spi}))
        return pmt.cons(meta, pmt.init_u8vector(len(payload), payload))

    def _request_id(self, db_call_msg):
        return pmt.dict_ref(pmt.car(db_call_msg), pmt.intern("db_request_id"), pmt.PMT_NIL)

    def _mk_response(self, request_id, sdls_counter):
        meta = self._mk_meta({
            "auth_key": pmt.intern(self.AUTH_KEY),
            "crypt_key": pmt.intern(self.CRYPT_KEY),
            "vcid": 0x12,
            "vcid_counter": 1,
            "sdls_counter": sdls_counter,
        })
        if request_id is not None:
            meta = pmt.dict_add(meta, pmt.intern("db_request_id"), request_id)
        return pmt.cons(meta, pmt.PMT_NIL)

    def _outputs(self, published):
        """(payload bytes, sdls_counter) for every PDU published on 'out'."""
        result = []
        for port, msg in published:
            if pmt.eqv(port, pmt.intern("out")):
                payload = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
                value = self._get_nested_value(pmt.car(msg), ["sdls", "security_header", "sdls_counter"])
                counter = int(pmt.to_uint64(value)) if pmt.is_uint64(value) else int(pmt.to_long(value))
                result.append((payload, counter))
        return result

    def _db_calls(self, published):
        return [msg for port, msg in published if pmt.eqv(port, pmt.intern("db_call"))]

    def test_019_each_request_gets_a_distinct_request_id(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            block.send_db_call(self._mk_request(1, [0xB]))
        finally:
            self._restore_pub(block, original_pub)

        calls = self._db_calls(published)
        self.assertEqual(len(calls), 2)
        id_a, id_b = self._request_id(calls[0]), self._request_id(calls[1])
        self.assertFalse(pmt.eqv(id_a, pmt.PMT_NIL))
        self.assertFalse(pmt.eqv(id_a, id_b))

    def test_020_two_in_flight_requests_answered_in_order(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            block.send_db_call(self._mk_request(1, [0xB]))
            call_a, call_b = self._db_calls(published)
            block.send_msg_out(self._mk_response(self._request_id(call_a), 100))
            block.send_msg_out(self._mk_response(self._request_id(call_b), 200))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(self._outputs(published), [(bytes([0xA]), 100), (bytes([0xB]), 200)])

    def test_021_two_in_flight_requests_answered_in_reverse_order(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            block.send_db_call(self._mk_request(1, [0xB]))
            call_a, call_b = self._db_calls(published)
            block.send_msg_out(self._mk_response(self._request_id(call_b), 200))
            block.send_msg_out(self._mk_response(self._request_id(call_a), 100))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(self._outputs(published), [(bytes([0xB]), 200), (bytes([0xA]), 100)])

    def test_022_unanswered_request_does_not_misalign_later_ones(self):
        # db_client drops some queries without answering (unknown SCID/SPI,
        # exhausted sdls_counter, ...). A later answer must still reach
        # its own request, not the unanswered one.
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))  # never answered
            block.send_db_call(self._mk_request(1, [0xB]))
            _, call_b = self._db_calls(published)
            block.send_msg_out(self._mk_response(self._request_id(call_b), 200))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(self._outputs(published), [(bytes([0xB]), 200)])

    def test_023_unknown_request_id_is_dropped(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            (call_a,) = self._db_calls(published)
            block.send_msg_out(self._mk_response(pmt.from_uint64(999999), 100))
            # The real answer still finds its pending request afterwards.
            block.send_msg_out(self._mk_response(self._request_id(call_a), 100))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(self._outputs(published), [(bytes([0xA]), 100)])

    def test_024_request_id_is_not_forwarded_downstream(self):
        block = inject_db()
        original_pub, published = self._capture_pub(block)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            (call_a,) = self._db_calls(published)
            block.send_msg_out(self._mk_response(self._request_id(call_a), 100))
        finally:
            self._restore_pub(block, original_pub)

        outs = [msg for port, msg in published if pmt.eqv(port, pmt.intern("out"))]
        self.assertEqual(len(outs), 1)
        self.assertFalse(pmt.dict_has_key(pmt.car(outs[0]), pmt.intern("db_request_id")))

    def test_025_pending_requests_are_bounded(self):
        # Requests that are never answered must not accumulate forever:
        # beyond MAX_PENDING_REQUESTS the oldest is evicted, so its late
        # answer is dropped while the newest still pairs correctly.
        block = inject_db()
        limit = inject_db.MAX_PENDING_REQUESTS
        original_pub, published = self._capture_pub(block)
        try:
            for i in range(limit + 1):
                block.send_db_call(self._mk_request(1, [i & 0xFF]))
            calls = self._db_calls(published)
            block.send_msg_out(self._mk_response(self._request_id(calls[0]), 1))
            block.send_msg_out(self._mk_response(self._request_id(calls[-1]), 2))
        finally:
            self._restore_pub(block, original_pub)

        self.assertEqual(len(block._pending), limit - 1)
        self.assertEqual(self._outputs(published), [(bytes([limit & 0xFF]), 2)])

    def test_026_real_db_client_round_trip_with_two_in_flight_requests(self):
        # End to end with a real db_client: two requests for different SPIs
        # are both queried before either answer is processed, and the
        # answers are processed in reverse order. Each frame must still get
        # its own entry's counter.
        import tempfile
        from pathlib import Path
        from gnuradio.soarr import db_client

        yaml_content = f"""
entries:
  "a":
    SCID: 341
    SPI: 1
    crypt_key: "{self.CRYPT_KEY}"
    auth_key: "{self.AUTH_KEY}"
    sdls_counter: 100
  "b":
    SCID: 341
    SPI: 2
    crypt_key: "{self.CRYPT_KEY}"
    auth_key: "{self.AUTH_KEY}"
    sdls_counter: 200
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "db.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")
            client = db_client(type=1, yaml_path=str(yaml_path), forward_body=False)

        block = inject_db()
        original_pub, published = self._capture_pub(block)
        original_client_pub, client_published = self._capture_pub(client)
        try:
            block.send_db_call(self._mk_request(1, [0xA]))
            block.send_db_call(self._mk_request(2, [0xB]))
            for call in self._db_calls(published):
                client.make_db_call(call)
            for _, response in reversed(client_published):
                block.send_msg_out(response)
        finally:
            self._restore_pub(block, original_pub)
            self._restore_pub(client, original_client_pub)

        self.assertEqual(self._outputs(published), [(bytes([0xB]), 200), (bytes([0xA]), 100)])


if __name__ == '__main__':
    gr_unittest.run(qa_inject_db)
