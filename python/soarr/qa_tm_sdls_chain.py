#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""The TM chain with SDLS, TX to RX in one flowgraph: the SDLS-protected
data travels in an encapsulation packet, so the TM frame layer can
delimit it. Between the frame generator and the frame decoder the
metadata is cleared, as on a real link: RX gets SCID, SPI and counter
from the received bytes only."""

import time

from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import (data_creator, encapsulation_header, encapsulation_parser, key_database, sdls_apply,
                            sdls_header_parser, sdls_process, tm_frame_decoder, tm_frame_generator)

SCID = 0x155  # the Key Database's dummy entry
SPI = 1
PAYLOAD = bytes(range(256))


class _link(gr.basic_block):
    """Frames over the air: only the bytes arrive; optionally one byte of
    the first frame is flipped."""

    def __init__(self, flip_at=None):
        gr.basic_block.__init__(self, name="link", in_sig=None, out_sig=None)
        self.flip_at = flip_at
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self._send)

    def _send(self, msg):
        frame = bytearray(pmt.u8vector_elements(pmt.cdr(msg)))
        if self.flip_at is not None:
            frame[self.flip_at] ^= 0x01
            self.flip_at = None
        self.message_port_pub(pmt.intern("out"), pmt.cons(pmt.make_dict(), pmt.init_u8vector(len(frame), list(frame))))


class qa_tm_sdls_chain(gr_unittest.TestCase):

    def _run(self, mode, flip_at=None, expected=1, timeout_s=3.0):
        """One payload from the Data Creator through TX, link and RX; the
        payloads that come out."""
        source = data_creator(data=PAYLOAD, scid=SCID, spi=SPI)
        chain = [source, key_database(role="tx", scid=SCID, spi=SPI), sdls_apply(mode=mode), encapsulation_header(),
                 tm_frame_generator(scid=SCID, frame_length=223), _link(flip_at), tm_frame_decoder(),
                 encapsulation_parser(), sdls_header_parser(), key_database(role="rx", scid=SCID, spi=SPI),
                 sdls_process(mode=mode)]
        sink = blocks.message_debug()
        tb = gr.top_block()
        for a, b in zip(chain, chain[1:]):
            tb.msg_connect((a, "out"), (b, "in"))
        tb.msg_connect((chain[-1], "out"), (sink, "store"))
        tb.start()
        source.to_basic_block()._post(pmt.intern("ping"), pmt.PMT_T)
        deadline = time.time() + timeout_s
        while sink.num_messages() < expected and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        tb.stop()
        tb.wait()
        return [bytes(pmt.u8vector_elements(pmt.cdr(sink.get_message(i)))) for i in range(sink.num_messages())]

    def test_001_round_trip_in_every_mode(self):
        for mode in (1, 2, 3):  # encryption only, authentication only, both
            self.assertEqual(self._run(mode), [PAYLOAD], f"mode {mode}")

    # A flipped byte in the protected data fails the MAC check.
    def test_002_altered_frame_is_dropped(self):
        data_field = 6  # the frame's primary header
        encapsulation_and_security_header = 4 + 4
        self.assertEqual(self._run(3, flip_at=data_field + encapsulation_and_security_header + 50, expected=0,
                                   timeout_s=0.5), [])


if __name__ == '__main__':
    gr_unittest.run(qa_tm_sdls_chain)
