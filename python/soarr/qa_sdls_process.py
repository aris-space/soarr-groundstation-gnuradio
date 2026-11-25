#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time

from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import sdls_process
from gnuradio.soarr.db_client import TEST_AUTH_KEY, TEST_CRYPT_KEY

NONCE = bytes(range(1, 15))  # non-zero, so it must reach both inner blocks
COUNTER = 0x1234
PLAINTEXT = bytes([0x10, 0x20, 0x30, 0x40, 0x50])


def _encrypt(data):
    return AES.new(bytes.fromhex(TEST_CRYPT_KEY), AES.MODE_CTR, nonce=NONCE, initial_value=COUNTER).encrypt(data)


def _mac(data):
    cmac = CMAC.new(bytes.fromhex(TEST_AUTH_KEY), ciphermod=AES)
    cmac.update(NONCE + COUNTER.to_bytes(2, "big") + data)
    return cmac.digest()


class qa_sdls_process(gr_unittest.TestCase):

    def _pdu(self, payload):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(TEST_CRYPT_KEY))
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(TEST_AUTH_KEY))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(COUNTER))
        return pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload)))

    def _run(self, mode, payload):
        """A PDU into sdls_process's own in port, in a running flowgraph;
        returns every PDU it published."""
        tb = gr.top_block()
        source = blocks.message_strobe(self._pdu(payload), 50)
        dut = sdls_process(mode=mode, nonce=NONCE)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (dut, "in"))
        tb.msg_connect((dut, "out"), (sink, "store"))
        tb.start()
        for _ in range(30):  # 300 ms: several strobes, enough for any output
            if sink.num_messages():
                break
            time.sleep(0.01)
        time.sleep(0.1)
        tb.stop()
        tb.wait()
        return [sink.get_message(i) for i in range(sink.num_messages())]

    def _plaintext(self, outputs):
        self.assertGreaterEqual(len(outputs), 1)
        return bytes(pmt.u8vector_elements(pmt.cdr(outputs[0])))

    def test_001_verify_and_decrypt(self):
        ciphertext = _encrypt(PLAINTEXT)
        self.assertEqual(self._plaintext(self._run(3, ciphertext + _mac(ciphertext))), PLAINTEXT)

    def test_002_decrypt_only(self):
        self.assertEqual(self._plaintext(self._run(1, _encrypt(PLAINTEXT))), PLAINTEXT)

    def test_003_verify_only(self):
        self.assertEqual(self._plaintext(self._run(2, PLAINTEXT + _mac(PLAINTEXT))), PLAINTEXT)

    # A frame whose MAC doesn't verify never reaches decryption or out.
    def test_004_tampered_mac_is_dropped(self):
        ciphertext = _encrypt(PLAINTEXT)
        tampered = ciphertext + bytes([_mac(ciphertext)[0] ^ 0x01]) + _mac(ciphertext)[1:]
        for mode in (2, 3):
            self.assertEqual(self._run(mode, tampered), [], f"mode {mode}")

    def test_005_keys_are_removed(self):
        ciphertext = _encrypt(PLAINTEXT)
        meta = pmt.car(self._run(3, ciphertext + _mac(ciphertext))[0])
        self.assertFalse(pmt.dict_has_key(meta, pmt.intern("crypt_key")))
        self.assertFalse(pmt.dict_has_key(meta, pmt.intern("auth_key")))

    def test_006_invalid_mode_raises(self):
        for mode in (0, 4):
            with self.assertRaises(ValueError):
                sdls_process(mode=mode)


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_process)
