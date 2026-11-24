#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import sdls_header, sdls_header_parser


class qa_sdls_header_parser(gr_unittest.TestCase):

    def _call(self, block, handler, msg):
        out = []
        block.message_port_pub = lambda port, m: out.append(m)
        getattr(block, handler)(msg)
        return out

    def _pdu(self, data, meta=None):
        meta = pmt.make_dict() if meta is None else meta
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _security_header(self, msg, key):
        sdls = pmt.dict_ref(pmt.car(msg), pmt.intern("sdls"), pmt.PMT_NIL)
        header = pmt.dict_ref(sdls, pmt.intern("security_header"), pmt.PMT_NIL)
        return pmt.to_long(pmt.dict_ref(header, pmt.intern(key), pmt.PMT_NIL))

    # What the SDLS Header Adder puts in front comes back as metadata; the
    # rest of the payload and the other metadata stay as they are.
    def test_001_round_trip_with_the_header_adder(self):
        body = bytes(range(40))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("spi"), pmt.from_long(0x0102))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(0xBEEF))
        meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(341))
        (framed,) = self._call(sdls_header(), "add_header", self._pdu(body, meta))
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(framed)))[:4], bytes.fromhex("0102BEEF"))

        (msg,) = self._call(sdls_header_parser(), "parse_header", framed)
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(msg))), body)
        self.assertEqual([self._security_header(msg, k) for k in
                          ("spi", "security_param_index", "sdls_counter", "initialization_vector")],
                         [0x0102, 0x0102, 0xBEEF, 0xBEEF])
        self.assertEqual(pmt.to_long(pmt.dict_ref(pmt.car(msg), pmt.intern("scid"), pmt.PMT_NIL)), 341)

        (empty,) = self._call(sdls_header_parser(), "parse_header", self._pdu(bytes.fromhex("00010002")))
        self.assertEqual(pmt.length(pmt.cdr(empty)), 0)  # a header alone is fine

    # Keys already under sdls (e.g. a security trailer) are kept.
    def test_002_other_sdls_keys_are_kept(self):
        trailer = pmt.init_u8vector(2, [7, 8])
        sdls = pmt.dict_add(pmt.make_dict(), pmt.intern("security_trailer"), trailer)
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("sdls"), sdls)
        (msg,) = self._call(sdls_header_parser(), "parse_header", self._pdu(bytes.fromhex("0001000509"), meta))
        kept = pmt.dict_ref(pmt.dict_ref(pmt.car(msg), pmt.intern("sdls"), pmt.PMT_NIL),
                            pmt.intern("security_trailer"), pmt.PMT_NIL)
        self.assertTrue(pmt.equal(kept, trailer))
        self.assertEqual(self._security_header(msg, "sdls_counter"), 5)

    def test_003_malformed_input_is_dropped(self):
        for msg in (pmt.intern("not a pdu"), pmt.cons(pmt.intern("no dict"), pmt.init_u8vector(4, [0] * 4)),
                    pmt.cons(pmt.make_dict(), pmt.intern("not bytes")), self._pdu(bytes(3)), self._pdu(b"")):
            self.assertEqual(self._call(sdls_header_parser(), "parse_header", msg), [], str(msg))

    def test_004_publish_failure_is_dropped_not_raised(self):
        block = sdls_header_parser()

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        block.message_port_pub = _raise
        block.parse_header(self._pdu(bytes(8)))  # must not raise


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_header_parser)
