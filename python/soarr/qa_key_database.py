#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time
from pathlib import Path

from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import key_database
from gnuradio.soarr.db_client import TEST_CRYPT_KEY

EXAMPLE_DB = Path(__file__).resolve().parents[2] / "examples" / "db_client_example.yaml"


def _nested(meta, path):
    for key in path:
        meta = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    return meta


class qa_key_database(gr_unittest.TestCase):

    def _request(self, spi=1, sdls_counter=None):
        """Metadata in the nested shape data_creator (TX) or ccsds_reader
        (RX, with the frame's counter) produces."""
        tc = pmt.make_dict()
        for key, value in (("scid", 341), ("vcid", 0x12), ("vcid_counter", 7)):
            tc = pmt.dict_add(tc, pmt.intern(key), pmt.from_long(value))
        tc = pmt.dict_add(tc, pmt.intern("bypass_flag"), pmt.from_bool(False))
        tc = pmt.dict_add(tc, pmt.intern("control_flag"), pmt.from_bool(False))
        header = pmt.dict_add(pmt.make_dict(), pmt.intern("spi"), pmt.from_long(spi))
        if sdls_counter is not None:
            header = pmt.dict_add(header, pmt.intern("sdls_counter"), pmt.from_long(sdls_counter))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("telecommand"), pmt.dict_add(pmt.make_dict(), pmt.intern("tc_header"), tc))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), pmt.dict_add(pmt.make_dict(), pmt.intern("security_header"), header))
        return pmt.cons(meta, pmt.init_u8vector(3, [1, 2, 3]))

    def _run(self, dut, request, count):
        """Send the request repeatedly into the block's in port until
        `count` answers came out; return them."""
        tb = gr.top_block()
        source = blocks.message_strobe(request, 20)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (dut, "in"))
        tb.msg_connect((dut, "out"), (sink, "store"))
        tb.start()
        for _ in range(300):
            if sink.num_messages() >= count:
                break
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(sink.num_messages(), count)
        return [sink.get_message(i) for i in range(count)]

    def _sdls_counter(self, msg):
        value = _nested(pmt.car(msg), ["sdls", "security_header", "sdls_counter"])
        return int(pmt.to_uint64(value)) if pmt.is_uint64(value) else int(pmt.to_long(value))

    # TX: the keys come from the database and its counter advances from one
    # message to the next - the db_call/db_callback loop works inside.
    def test_001_tx_merges_keys_and_counts(self):
        outputs = self._run(key_database(role="tx"), self._request(), 2)
        self.assertEqual([self._sdls_counter(m) for m in outputs], [0, 1])
        crypt_key = pmt.dict_ref(pmt.car(outputs[0]), pmt.intern("crypt_key"), pmt.PMT_NIL)
        self.assertEqual(pmt.symbol_to_string(crypt_key), TEST_CRYPT_KEY)
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(outputs[0]))), bytes([1, 2, 3]))

    # RX: the frame's counter is kept, whatever the database's.
    def test_002_rx_keeps_the_frames_counter(self):
        outputs = self._run(key_database(role="rx"), self._request(sdls_counter=500), 2)
        self.assertEqual([self._sdls_counter(m) for m in outputs], [500, 500])

    def test_003_yaml_database(self):
        outputs = self._run(key_database(role="tx", db_type=1, yaml_path=str(EXAMPLE_DB)), self._request(spi=2), 1)
        self.assertEqual(self._sdls_counter(outputs[0]), 120)  # the example entry's starting counter
        crypt_key = pmt.symbol_to_string(pmt.dict_ref(pmt.car(outputs[0]), pmt.intern("crypt_key"), pmt.PMT_NIL))
        self.assertEqual(crypt_key, "11223344556677889900AABBCCDDEEFF11223344556677889900AABBCCDDEEFF")

    # Only the dummy and YAML databases exist; the remote one isn't built.
    def test_004_invalid_settings_raise(self):
        for kwargs in ({"db_type": 2}, {"db_type": -1}, {"role": "both"}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                key_database(**kwargs)


if __name__ == '__main__':
    gr_unittest.run(qa_key_database)
