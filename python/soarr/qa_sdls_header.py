#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from unittest.mock import patch

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import sdls_header


class qa_sdls_header(gr_unittest.TestCase):

    def setUp(self):
        self.block = sdls_header()
        self.published = []

    def tearDown(self):
        self.block = None
        self.published = []

    def test_instance(self):
        instance = sdls_header()
        self.assertIsNotNone(instance)

    def _capture_pub(self):
        original_pub = self.block.message_port_pub

        def _capture(port, msg):
            self.published.append((port, msg))

        self.block.message_port_pub = _capture
        return original_pub

    def _restore_pub(self, original_pub):
        self.block.message_port_pub = original_pub

    def _make_pdu(self, payload_bytes, spi, sdls_counter, extra_meta=None):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.intern(spi.hex().upper()))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.intern(sdls_counter.hex().upper()))
        if extra_meta is not None:
            for key, value in extra_meta:
                meta = pmt.dict_add(meta, pmt.intern(key), value)
        payload = pmt.init_u8vector(len(payload_bytes), list(payload_bytes))
        return pmt.cons(meta, payload)

    def test_001_prepends_header_and_preserves_other_metadata(self):
        spi = bytes([0x12, 0x34])
        sdls_counter = bytes([0xAA, 0xBB])
        payload = bytes([0x01, 0x02, 0x03, 0x04])
        msg = self._make_pdu(
            payload,
            spi=spi,
            sdls_counter=sdls_counter,
            extra_meta=[("frame_id", pmt.from_long(7))],
        )

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("spi")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("sdls_counter")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, spi + sdls_counter + payload)

    def test_002_short_fields_are_zero_padded(self):
        spi = bytes([0x12])
        sdls_counter = bytes([0xAA])
        payload = bytes([0x10, 0x20])
        msg = self._make_pdu(payload, spi=spi, sdls_counter=sdls_counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(self.published[0][1])))
        self.assertEqual(out_payload, b"\x12\x00\xAA\x00" + payload)

    def test_003_too_long_spi_emits_no_output(self):
        spi = bytes([0x01, 0x02, 0x03])
        sdls_counter = bytes([0xAA, 0xBB])
        payload = bytes([0x10, 0x20])
        msg = self._make_pdu(payload, spi=spi, sdls_counter=sdls_counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_004_missing_spi_emits_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.intern("AABB"))
        payload = pmt.init_u8vector(2, [0x10, 0x20])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_005_non_u8vector_payload_emits_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.intern("1234"))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.intern("AABB"))
        msg = pmt.cons(meta, pmt.from_long(99))

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_006_too_long_iv_emits_no_output(self):
        spi = bytes([0x12, 0x34])
        sdls_counter = bytes([0xAA, 0xBB, 0xCC])
        payload = bytes([0x10, 0x20])
        msg = self._make_pdu(payload, spi=spi, sdls_counter=sdls_counter)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_007_invalid_hex_symbols_emit_no_output(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.intern("ZZZZ"))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.intern("AABB"))
        payload = pmt.init_u8vector(2, [0x10, 0x20])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_008_non_pair_message_emits_no_output(self):
        original_pub = self._capture_pub()
        try:
            self.block.add_header(pmt.PMT_T)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)

    def test_009_u8vector_spi_and_iv_are_supported(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.init_u8vector(2, [0x12, 0x34]))
        meta = pmt.dict_add(
            meta,
            pmt.intern("sdls_counter"),
            pmt.init_u8vector(2, [0xAA, 0xBB]),
        )
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(11))
        payload = pmt.init_u8vector(3, [0x01, 0x02, 0x03])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_port, out_msg = self.published[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("spi")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("sdls_counter")))

        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))
        self.assertEqual(out_payload, b"\x12\x34\xAA\xBB\x01\x02\x03")

    def test_010_integer_spi_is_supported(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(1))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.intern("AABB"))
        payload = pmt.init_u8vector(2, [0x10, 0x20])
        msg = pmt.cons(meta, payload)

        original_pub = self._capture_pub()
        try:
            self.block.add_header(msg)
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 1)
        out_payload = bytes(pmt.u8vector_elements(pmt.cdr(self.published[0][1])))
        self.assertEqual(out_payload, b"\x00\x01\xAA\xBB\x10\x20")

    # Additional: iv_length_bytes is rejected at construction time,
    # matching the GRC block.yml's own asserts range (0-16), instead of
    # being silently accepted and only failing later/differently.
    def test_011_iv_length_bytes_out_of_range_raises_at_construction(self):
        with self.assertRaises(ValueError):
            sdls_header(iv_length_bytes=17)
        with self.assertRaises(ValueError):
            sdls_header(iv_length_bytes=-1)

    # Additional: an internal failure past spi/counter extraction (e.g. a
    # future encoding-library incompatibility) is caught, logged, and
    # dropped - not left to raise out of the real message handler.
    def test_012_internal_build_failure_is_dropped_not_raised(self):
        spi = bytes([0x12, 0x34])
        sdls_counter = bytes([0xAA, 0xBB])
        payload = bytes([0x01, 0x02, 0x03])
        msg = self._make_pdu(payload, spi=spi, sdls_counter=sdls_counter)

        original_pub = self._capture_pub()
        try:
            with patch.object(
                self.block._header_struct,
                "build",
                side_effect=RuntimeError("simulated build failure"),
            ):
                self.block.add_header(msg)  # must not raise
        finally:
            self._restore_pub(original_pub)

        self.assertEqual(len(self.published), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_header)
