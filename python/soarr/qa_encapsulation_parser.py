#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import encapsulation_header, encapsulation_parser
from gnuradio.soarr import encapsulation_packet as ep


class qa_encapsulation_parser(gr_unittest.TestCase):

    def setUp(self):
        self.block = encapsulation_parser()

    def _run(self, msg, block=None):
        block = block or self.block
        published = []
        original = block.message_port_pub
        block.message_port_pub = lambda port, out: published.append((port, out))
        try:
            block.parse_packets(msg)
        finally:
            block.message_port_pub = original
        return published

    def _pdu(self, data, meta=None):
        meta = meta if meta is not None else pmt.make_dict()
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _encapsulate(self, payload, user_defined_field=0):
        """Wrap payload with the real TX block, as the transmitter does."""
        tx = encapsulation_header(user_defined_field)
        out = []
        tx.message_port_pub = lambda port, msg: out.append(msg)
        tx.add_header(self._pdu(payload))
        return bytes(pmt.u8vector_elements(pmt.cdr(out[0])))

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def _field(self, msg, key):
        header = pmt.dict_ref(pmt.car(msg), pmt.intern("encapsulation_header"), pmt.PMT_NIL)
        return pmt.dict_ref(header, pmt.intern(key), pmt.PMT_NIL)

    def test_instance(self):
        self.assertIsNotNone(encapsulation_parser())

    def test_001_round_trip_every_header_variant(self):
        for size in (1, 10, 253, 254, 1000, 65532):
            with self.subTest(size=size):
                payload = bytes(i & 0xFF for i in range(size))
                published = self._run(self._pdu(self._encapsulate(payload, user_defined_field=5)))
                self.assertEqual(len(published), 1)
                port, msg = published[0]
                self.assertTrue(pmt.eqv(port, pmt.intern("out")))
                self.assertEqual(self._payload(msg), payload)
                self.assertEqual(pmt.to_long(self._field(msg, "packet_length")), len(self._encapsulate(payload)))

    def test_002_header_fields_in_metadata_input_metadata_kept(self):
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("frame_id"), pmt.from_long(7))
        published = self._run(self._pdu(self._encapsulate(bytes(300), user_defined_field=9), meta))
        _, msg = published[0]
        self.assertEqual(pmt.to_long(pmt.dict_ref(pmt.car(msg), pmt.intern("frame_id"), pmt.PMT_NIL)), 7)
        self.assertEqual(pmt.to_long(self._field(msg, "length_of_length")), 0b10)
        self.assertEqual(pmt.to_long(self._field(msg, "protocol_id")), ep.PROTOCOL_ID_DATA)
        self.assertEqual(pmt.to_long(self._field(msg, "user_defined_field")), 9)
        # Fields the 4-byte variant doesn't carry are left out, not published as None
        header = pmt.dict_ref(pmt.car(msg), pmt.intern("encapsulation_header"), pmt.PMT_NIL)
        self.assertFalse(pmt.dict_has_key(header, pmt.intern("ccsds_defined_field")))

    def test_003_idle_packets_produce_no_output(self):
        idle = ep.header_for_payload(0)  # 1-byte idle packet
        self.assertEqual(self._run(self._pdu(idle)), [])
        # Idle fill after a data packet is skipped too
        published = self._run(self._pdu(self._encapsulate(b"abc") + idle + idle))
        self.assertEqual([self._payload(m) for _, m in published], [b"abc"])

    def test_004_several_packets_in_one_pdu(self):
        data = self._encapsulate(b"first") + self._encapsulate(bytes(400)) + self._encapsulate(b"x")
        published = self._run(self._pdu(data))
        self.assertEqual([self._payload(m) for _, m in published], [b"first", bytes(400), b"x"])

    def test_005_truncated_packet_is_dropped(self):
        packet = self._encapsulate(bytes(50))
        self.assertEqual(self._run(self._pdu(packet[:-1])), [])  # packet length exceeds data
        self.assertEqual(self._run(self._pdu(packet[:1])), [])    # header itself cut off
        # Packets before a truncated one are still delivered
        published = self._run(self._pdu(self._encapsulate(b"ok") + packet[:-1]))
        self.assertEqual([self._payload(m) for _, m in published], [b"ok"])

    def test_006_packet_length_shorter_than_header_is_dropped(self):
        bad = ep.build_header({"length_of_length": 0b01, "packet_length": 1}) + b"zz"
        self.assertEqual(self._run(self._pdu(bad)), [])

    def test_007_malformed_input_dropped_not_raised(self):
        self.assertEqual(self._run(pmt.intern("not-a-pair")), [])
        self.assertEqual(self._run(pmt.cons(pmt.make_dict(), pmt.intern("x"))), [])
        # (PMT_NIL counts as an empty dict in PMT, so use a real non-dict)
        self.assertEqual(self._run(pmt.cons(pmt.from_long(3), pmt.init_u8vector(3, [0xFD, 3, 1]))), [])
        self.assertEqual(self._run(self._pdu(b"")), [])

    def test_008_internal_publish_failure_is_dropped_not_raised(self):
        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")
        original = self.block.message_port_pub
        self.block.message_port_pub = _raise
        try:
            self.block.parse_packets(self._pdu(self._encapsulate(b"abc")))  # must not raise
        finally:
            self.block.message_port_pub = original


if __name__ == '__main__':
    gr_unittest.run(qa_encapsulation_parser)
