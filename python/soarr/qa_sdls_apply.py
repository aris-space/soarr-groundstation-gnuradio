#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time

from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import sdls_apply, sdls_authentication, sdls_encryption, sdls_header
from gnuradio.soarr.db_client import TEST_AUTH_KEY, TEST_CRYPT_KEY

NONCE = bytes(range(1, 15))  # non-zero, so it must reach both inner blocks
PAYLOAD = bytes([0x10, 0x20, 0x30, 0x40, 0x50])


class qa_sdls_apply(gr_unittest.TestCase):

    def _pdu(self):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(TEST_CRYPT_KEY))
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(TEST_AUTH_KEY))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(0x1234))
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(1))
        return pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD), list(PAYLOAD)))

    def _chained(self, encryption, authentication):
        """The three blocks called one after another, as the reference."""
        msg = self._pdu()
        for block, handler in ((sdls_encryption(encryption, NONCE), "add_encryption"),
                               (sdls_authentication(authentication, NONCE), "add_authentication"),
                               (sdls_header(), "add_header")):
            out = []
            block.message_port_pub = lambda port, m: out.append(m)
            getattr(block, handler)(msg)
            self.assertEqual(len(out), 1)
            msg = out[0]
        return msg

    def _run(self, mode):
        """A PDU into sdls_apply's own in port, in a running flowgraph."""
        tb = gr.top_block()
        source = blocks.message_strobe(self._pdu(), 50)
        dut = sdls_apply(mode=mode, nonce=NONCE)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (dut, "in"))
        tb.msg_connect((dut, "out"), (sink, "store"))
        tb.start()
        for _ in range(200):
            if sink.num_messages():
                break
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(sink.num_messages(), 1)
        return sink.get_message(0)

    def _assert_same(self, got, expected):
        self.assertEqual(pmt.u8vector_elements(pmt.cdr(got)), pmt.u8vector_elements(pmt.cdr(expected)))
        self.assertTrue(pmt.equal(pmt.car(got), pmt.car(expected)))

    def test_001_encryption_and_authentication(self):
        self._assert_same(self._run(3), self._chained(True, True))

    def test_002_encryption_only(self):
        self._assert_same(self._run(1), self._chained(True, False))

    def test_003_authentication_only(self):
        self._assert_same(self._run(2), self._chained(False, True))

    # The keys never leave the block: each inner block drops its own.
    def test_004_keys_are_removed(self):
        meta = pmt.car(self._run(3))
        self.assertFalse(pmt.dict_has_key(meta, pmt.intern("crypt_key")))
        self.assertFalse(pmt.dict_has_key(meta, pmt.intern("auth_key")))

    # Without SDLS the block isn't used, so only modes 1-3 exist.
    def test_005_invalid_mode_raises(self):
        for mode in (0, 4):
            with self.assertRaises(ValueError):
                sdls_apply(mode=mode)


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_apply)
