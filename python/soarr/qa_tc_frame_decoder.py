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

from gnuradio.soarr import (encapsulation_header, encapsulation_parser, key_database, sdls_apply,
                            sdls_process, tc_frame_decoder, tc_frame_encoder)
from gnuradio.soarr.qa_tc_frame_encoder import _cltu, _crc16_ccitt_false, _tc_header

SCID, VCID = 0x155, 0x12
IDLE_BITS = [0, 1] * 64  # carrier idle around the CLTU


def _bits(data):
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _bit_stream(tb, cltus):
    """The CLTUs as a bit stream that keeps running, as a receiver's does:
    repeated, with carrier idle between them, at a throttled rate. A stream
    that ends would shut the flowgraph down - message blocks included -
    before the later message hops had run."""
    bits = []
    for cltu in cltus:
        bits += IDLE_BITS + _bits(cltu)
    source = blocks.vector_source_b(bits + IDLE_BITS, repeat=True)
    throttle = blocks.throttle(gr.sizeof_char, 200e3)
    tb.connect(source, throttle)
    return throttle


def _wait(sink, count, timeout_s=3.0):
    for _ in range(int(timeout_s * 100)):
        if sink.num_messages() >= count:
            return
        time.sleep(0.01)


class qa_tc_frame_decoder(gr_unittest.TestCase):

    def _encode(self, data, vcid_counter=0):
        """The TC Frame Encoder's CLTU for a data field."""
        tb = gr.top_block()
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("vcid_counter"), pmt.from_long(vcid_counter))
        source = blocks.message_strobe(pmt.cons(meta, pmt.init_u8vector(len(data), list(data))), 50)
        encoder = tc_frame_encoder(scid=SCID, vcid=VCID)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (encoder, "in"))
        tb.msg_connect((encoder, "out"), (sink, "store"))
        tb.start()
        _wait(sink, 1)
        tb.stop()
        tb.wait()
        return bytes(pmt.u8vector_elements(pmt.cdr(sink.get_message(0))))

    def _decode(self, cltus, count, **kwargs):
        """The CLTUs as a running bit stream into the TC Frame Decoder;
        everything it published once `count` outputs arrived."""
        tb = gr.top_block()
        decoder = tc_frame_decoder(**kwargs)
        sink = blocks.message_debug()
        tb.connect(_bit_stream(tb, cltus), decoder)
        tb.msg_connect((decoder, "out"), (sink, "store"))
        tb.start()
        _wait(sink, count)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(sink.num_messages(), count)
        return [sink.get_message(i) for i in range(sink.num_messages())]

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def test_001_round_trip_without_sdls(self):
        data = bytes(range(30))
        out = self._decode([self._encode(data, vcid_counter=9)], 1, sdls_mode=0, encapsulation_used=False)[0]
        self.assertEqual(self._payload(out), data)
        tc_header = pmt.dict_ref(pmt.dict_ref(pmt.car(out), pmt.intern("telecommand"), pmt.PMT_NIL),
                                 pmt.intern("tc_header"), pmt.PMT_NIL)
        self.assertEqual(pmt.to_long(pmt.dict_ref(tc_header, pmt.intern("scid"), pmt.PMT_NIL)), SCID)
        self.assertEqual(pmt.to_long(pmt.dict_ref(tc_header, pmt.intern("vcid_counter"), pmt.PMT_NIL)), 9)

    # Only crc_check's ok output is wired: a frame whose FECF is wrong but
    # whose codewords are fine reaches the CRC check and stops there.
    def test_002_frame_with_wrong_fecf_is_dropped(self):
        data = bytes(range(10))
        frame = _tc_header(len(data), fsn=1) + data
        bad = frame + (_crc16_ccitt_false(frame) ^ 0x0001).to_bytes(2, "big")
        good_data = bytes(range(10, 20))
        outputs = self._decode([_cltu(bad), self._encode(good_data)], 3, sdls_mode=0, encapsulation_used=False)
        # The stream repeats both CLTUs; only the good frame ever comes out.
        self.assertEqual({self._payload(m) for m in outputs}, {good_data})

    # The closed loop from hierarchical blocks: TX path to CLTU, the CLTU as
    # bits, and the RX path back to the original payload.
    def test_003_closed_loop_with_sdls(self):
        nonce = bytes(range(1, 15))
        payload = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x42])

        tc = pmt.make_dict()
        for key, value in (("scid", SCID), ("vcid", VCID), ("vcid_counter", 0)):
            tc = pmt.dict_add(tc, pmt.intern(key), pmt.from_long(value))
        tc = pmt.dict_add(tc, pmt.intern("bypass_flag"), pmt.from_bool(False))
        tc = pmt.dict_add(tc, pmt.intern("control_flag"), pmt.from_bool(False))
        header = pmt.dict_add(pmt.make_dict(), pmt.intern("spi"), pmt.from_long(1))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("telecommand"), pmt.dict_add(pmt.make_dict(), pmt.intern("tc_header"), tc))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), pmt.dict_add(pmt.make_dict(), pmt.intern("security_header"), header))

        tx = gr.top_block()
        source = blocks.message_strobe(pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload))), 50)
        tx_keys, encap, apply_, encoder = key_database(role="tx"), encapsulation_header(), sdls_apply(3, nonce), tc_frame_encoder()
        cltus = blocks.message_debug()
        tx.msg_connect((source, "strobe"), (tx_keys, "in"))
        tx.msg_connect((tx_keys, "out"), (encap, "in"))
        tx.msg_connect((encap, "out"), (apply_, "in"))
        tx.msg_connect((apply_, "out"), (encoder, "in"))
        tx.msg_connect((encoder, "out"), (cltus, "store"))
        tx.start()
        _wait(cltus, 1)
        tx.stop()
        tx.wait()
        cltu = bytes(pmt.u8vector_elements(pmt.cdr(cltus.get_message(0))))

        rx = gr.top_block()
        decoder = tc_frame_decoder(sdls_mode=3, encapsulation_used=True)
        rx_keys, process, parser = key_database(role="rx"), sdls_process(3, nonce), encapsulation_parser()
        sink = blocks.message_debug()
        rx.connect(_bit_stream(rx, [cltu]), decoder)
        rx.msg_connect((decoder, "out"), (rx_keys, "in"))
        rx.msg_connect((rx_keys, "out"), (process, "in"))
        rx.msg_connect((process, "out"), (parser, "in"))
        rx.msg_connect((parser, "out"), (sink, "store"))
        rx.start()
        _wait(sink, 1)
        rx.stop()
        rx.wait()
        self.assertGreaterEqual(sink.num_messages(), 1)
        self.assertEqual(self._payload(sink.get_message(0)), payload)

    def test_004_invalid_settings_raise(self):
        for kwargs in ({"threshold": -1}, {"threshold": 17}, {"sdls_mode": 4}, {"vcid": 64}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                tc_frame_decoder(**kwargs)


if __name__ == '__main__':
    gr_unittest.run(qa_tc_frame_decoder)
