#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time
import unittest

from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import tm_frame_generator

try:
    import satellites
except ImportError:  # the TC side needs no gr-satellites
    satellites = None

IDLE = 0xE0  # CCSDS 133.1-B: a one-byte Encapsulation Idle Packet
NO_PACKET_START = 0x7FF  # 132.0-B-3 4.1.2.7.6.4


def _crc16(data):
    """CRC-16/CCITT-FALSE bit by bit (132.0-B-3 4.1.6.2), independent of
    the GNU Radio CRC the block uses."""
    crc = 0xFFFF
    for byte in data:
        for bit in range(7, -1, -1):
            feedback = ((crc >> 15) & 1) ^ ((byte >> bit) & 1)
            crc = (crc << 1) & 0xFFFF
            if feedback:
                crc ^= 0x1021
    return crc


def _header(frame):
    """The primary header fields (132.0-B-3 figures 4-2 and 4-3), from the bits."""
    bits = "".join(format(byte, "08b") for byte in frame[:6])
    return {
        "version": int(bits[0:2], 2), "scid": int(bits[2:12], 2), "vcid": int(bits[12:15], 2),
        "ocf_flag": int(bits[15], 2), "mc_count": int(bits[16:24], 2), "vc_count": int(bits[24:32], 2),
        "secondary_header_flag": int(bits[32], 2), "sync_flag": int(bits[33], 2),
        "packet_order_flag": int(bits[34], 2), "segment_length_id": int(bits[35:37], 2),
        "fhp": int(bits[37:48], 2),
    }


class qa_tm_frame_generator(gr_unittest.TestCase):

    def _run(self, block, msg):
        published = []
        block.message_port_pub = lambda port, m: published.append(m)
        block.build_frames(msg)
        return published

    def _pdu(self, data, meta=None):
        meta = pmt.make_dict() if meta is None else meta
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _meta(self, **fields):
        meta = pmt.make_dict()
        for key, value in fields.items():
            if isinstance(value, bytes):
                value = pmt.init_u8vector(len(value), list(value))
            elif not isinstance(value, pmt.pmt_base):
                value = pmt.from_long(value)
            meta = pmt.dict_add(meta, pmt.intern(key), value)
        return meta

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def _burst_more(self, msg):
        return pmt.to_bool(pmt.dict_ref(pmt.car(msg), pmt.intern("burst_more"), pmt.PMT_NIL))

    # Version 00, SCID, VCID, the frame counts, and the data field status
    # for packets: synchronization flag 0, packet order flag 0, segment
    # length identifier 11 (4.1.2.7.3-4.1.2.7.5).
    def test_001_primary_header(self):
        (frame,) = self._run(tm_frame_generator(scid=0x2AB, vcid=5), self._pdu(bytes(10)))
        self.assertEqual(_header(self._payload(frame)), {
            "version": 0, "scid": 0x2AB, "vcid": 5, "ocf_flag": 0, "mc_count": 0, "vc_count": 0,
            "secondary_header_flag": 0, "sync_flag": 0, "packet_order_flag": 0,
            "segment_length_id": 0b11, "fhp": 0,
        })

    # A packet shorter than the data field: one frame of frame_length
    # bytes, the rest filled with Encapsulation Idle Packets (4.2.2.5).
    def test_002_one_frame_filled_with_idle_packets(self):
        packet = bytes(range(1, 11))
        (frame,) = self._run(tm_frame_generator(frame_length=223), self._pdu(packet))
        payload = self._payload(frame)
        self.assertEqual(len(payload), 223)
        self.assertEqual(payload[6:], packet + bytes([IDLE]) * (217 - len(packet)))
        self.assertFalse(self._burst_more(frame))

    # A longer packet is split across frames (4.2.2.2). The FHP points to
    # the first packet starting in the frame: 0 for the packet itself,
    # all ones where only its continuation is, and the first idle packet in
    # the last frame (4.1.2.7.6). burst_more marks every frame but the last.
    def test_003_split_across_frames(self):
        packet = bytes(i % 251 for i in range(500))
        frames = self._run(tm_frame_generator(frame_length=223), self._pdu(packet))
        self.assertEqual(len(frames), 3)
        payloads = [self._payload(f) for f in frames]
        self.assertTrue(all(len(p) == 223 for p in payloads))
        self.assertEqual([_header(p)["fhp"] for p in payloads], [0, NO_PACKET_START, 500 - 2 * 217])
        self.assertEqual(b"".join(p[6:] for p in payloads), packet + bytes([IDLE]) * (3 * 217 - 500))
        self.assertEqual([self._burst_more(f) for f in frames], [True, True, False])

    # Exactly filling its frames, a packet needs no idle packets, so no
    # packet starts in the last frame.
    def test_004_exact_fit_has_no_fill(self):
        packet = bytes(i % 7 for i in range(2 * 217))
        frames = self._run(tm_frame_generator(frame_length=223), self._pdu(packet))
        payloads = [self._payload(f) for f in frames]
        self.assertEqual([_header(p)["fhp"] for p in payloads], [0, NO_PACKET_START])
        self.assertEqual(b"".join(p[6:] for p in payloads), packet)
        (single,) = self._run(tm_frame_generator(frame_length=223), self._pdu(bytes(217)))
        self.assertEqual(self._payload(single)[6:], bytes(217))

    # Both counts run modulo 256 over all frames of the block (4.1.2.5,
    # 4.1.2.6); the VC count is kept per virtual channel, the MC count per
    # spacecraft.
    def test_005_frame_counts(self):
        block = tm_frame_generator(frame_length=20)  # 14-byte data field
        headers = [_header(self._payload(f)) for f in self._run(block, self._pdu(bytes(30)))]
        self.assertEqual([(h["mc_count"], h["vc_count"]) for h in headers], [(0, 0), (1, 1), (2, 2)])

        (frame,) = self._run(block, self._pdu(bytes(1), self._meta(vcid=3)))
        self.assertEqual((_header(self._payload(frame))["mc_count"], _header(self._payload(frame))["vc_count"]), (3, 0))
        (frame,) = self._run(block, self._pdu(bytes(1)))
        self.assertEqual((_header(self._payload(frame))["mc_count"], _header(self._payload(frame))["vc_count"]), (4, 3))
        (frame,) = self._run(block, self._pdu(bytes(1), self._meta(scid=7)))
        self.assertEqual((_header(self._payload(frame))["mc_count"], _header(self._payload(frame))["vc_count"]), (0, 0))

        counts = [_header(self._payload(f))["vc_count"] for f in self._run(block, self._pdu(bytes(14 * 260)))]
        self.assertEqual(counts, [(4 + i) % 256 for i in range(260)])
        (frame,) = self._run(block, self._pdu(bytes(1)))  # the wrap carries over to the next PDU
        self.assertEqual(_header(self._payload(frame))["vc_count"], (4 + 260) % 256)

    # SCID and VCID from the metadata replace the block's; the metadata is
    # passed on, with burst_more added.
    def test_006_metadata_overrides_scid_vcid_and_is_kept(self):
        meta = self._meta(scid=0x3FF, vcid=7, frame=12)
        (frame,) = self._run(tm_frame_generator(scid=1, vcid=1), self._pdu(bytes(5), meta))
        header = _header(self._payload(frame))
        self.assertEqual((header["scid"], header["vcid"]), (0x3FF, 7))
        self.assertTrue(pmt.equal(pmt.dict_delete(pmt.car(frame), pmt.intern("burst_more")), meta))

    # The OCF is 4 bytes right after the data field (4.1.5.1), flagged in
    # the header (4.1.2.4); the metadata may carry a new value per PDU.
    def test_007_operational_control_field(self):
        block = tm_frame_generator(frame_length=30, ocf=True, ocf_value=0x01020304)
        (frame,) = self._run(block, self._pdu(bytes([9]) * 5))
        payload = self._payload(frame)
        self.assertEqual(len(payload), 30)
        self.assertEqual(_header(payload)["ocf_flag"], 1)
        self.assertEqual(payload[6:26], bytes([9]) * 5 + bytes([IDLE]) * 15)
        self.assertEqual(payload[26:], bytes.fromhex("01020304"))
        (frame,) = self._run(block, self._pdu(bytes(5), self._meta(ocf=pmt.from_uint64(0xA1B2C3D4))))
        self.assertEqual(self._payload(frame)[26:], bytes.fromhex("A1B2C3D4"))
        (frame,) = self._run(block, self._pdu(bytes(5), self._meta(ocf=0x7FFFFFFF)))  # a pmt long works too
        self.assertEqual(self._payload(frame)[26:], bytes.fromhex("7FFFFFFF"))

    # Secondary header: identification byte (version 00, total length
    # minus one) and its data, right after the primary header (4.1.3); the
    # metadata may carry new data of the same length per PDU.
    def test_008_secondary_header(self):
        block = tm_frame_generator(frame_length=30, secondary_header=True, secondary_header_data="AABBCC")
        (frame,) = self._run(block, self._pdu(bytes([1]) * 3))
        payload = self._payload(frame)
        self.assertEqual(_header(payload)["secondary_header_flag"], 1)
        self.assertEqual(payload[6:10], bytes([0b00_000011]) + bytes.fromhex("AABBCC"))
        self.assertEqual(payload[10:], bytes([1]) * 3 + bytes([IDLE]) * 17)
        self.assertEqual(_header(payload)["fhp"], 0)  # counted from the data field
        (frame,) = self._run(block, self._pdu(bytes(3), self._meta(secondary_header=bytes.fromhex("010203"))))
        self.assertEqual(self._payload(frame)[7:10], bytes.fromhex("010203"))

    # The FECF is the last 2 bytes: CRC-16/CCITT-FALSE over everything
    # before it (4.1.6), here with every optional field in its place.
    def test_009_fecf_and_field_order(self):
        self.assertEqual(_crc16(b"123456789"), 0x29B1)  # the CRC's check value
        block = tm_frame_generator(frame_length=40, fecf=True, ocf=True, ocf_value=0x11223344,
                                   secondary_header=True, secondary_header_data="55")
        frames = self._run(block, self._pdu(bytes(range(40))))
        self.assertEqual(len(frames), 2)  # data field: 40 - 6 - 2 - 4 - 2 = 26 bytes
        for frame in frames:
            payload = self._payload(frame)
            self.assertEqual(len(payload), 40)
            self.assertEqual(payload[6:8], bytes([0x01, 0x55]))
            self.assertEqual(payload[34:38], bytes.fromhex("11223344"))
            self.assertEqual(int.from_bytes(payload[38:], "big"), _crc16(payload[:38]))
        data = b"".join(self._payload(f)[8:34] for f in frames)
        self.assertEqual(data, bytes(range(40)) + bytes([IDLE]) * 12)
        self.assertEqual([_header(self._payload(f))["fhp"] for f in frames], [0, 14])

    def test_010_malformed_input_is_dropped(self):
        block = tm_frame_generator(ocf=True, secondary_header=True, secondary_header_data="0000")
        for msg in (pmt.intern("not a pdu"), pmt.cons(pmt.intern("no dict"), pmt.init_u8vector(1, [0])),
                    pmt.cons(pmt.make_dict(), pmt.intern("not bytes")), self._pdu(b""),
                    self._pdu(bytes(1), self._meta(scid=0x400)), self._pdu(bytes(1), self._meta(vcid=8)),
                    self._pdu(bytes(1), self._meta(vcid=pmt.intern("x"))),
                    self._pdu(bytes(1), self._meta(ocf=pmt.from_uint64(1 << 32))),
                    self._pdu(bytes(1), self._meta(secondary_header=bytes(3)))):
            self.assertEqual(self._run(block, msg), [], str(msg))
        (frame,) = self._run(block, self._pdu(bytes(1)))
        self.assertEqual(_header(self._payload(frame))["mc_count"], 0)  # dropped PDUs used no counts

    def test_011_publish_failure_is_dropped_not_raised(self):
        block = tm_frame_generator()

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        block.message_port_pub = _raise
        block.build_frames(self._pdu(bytes(10)))  # must not raise

    def test_012_invalid_settings_raise(self):
        for kwargs in ({"scid": 0x400}, {"scid": -1}, {"vcid": 8}, {"frame_length": 6},
                       {"frame_length": 2049}, {"frame_length": 12, "fecf": True, "ocf": True},
                       {"ocf": True, "ocf_value": 1 << 32}, {"ocf": True, "ocf_value": -1},
                       {"secondary_header": True, "secondary_header_data": ""},
                       {"secondary_header": True, "secondary_header_data": "00" * 64},
                       {"secondary_header": True, "secondary_header_data": "XY"}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                tm_frame_generator(**kwargs)
        tm_frame_generator(frame_length=7)  # one data byte is enough
        tm_frame_generator(frame_length=2048)

    # A 223-byte frame is one full RS(255,223) codeword through
    # gr-satellites' encoder, burst marker included.
    @unittest.skipIf(satellites is None, "gr-satellites is not installed (needed for TM Reed-Solomon)")
    def test_013_frames_fit_the_reed_solomon_encoder(self):
        generator = tm_frame_generator(frame_length=223)
        rs = satellites.encode_rs(True, 1)
        sink = blocks.message_debug()
        tb = gr.top_block()
        tb.msg_connect((generator, "out"), (rs, "in"))
        tb.msg_connect((rs, "out"), (sink, "store"))
        tb.start()
        generator.to_basic_block()._post(pmt.intern("in"), self._pdu(bytes(300)))
        deadline = time.time() + 2.0
        while sink.num_messages() < 2 and time.time() < deadline:
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        codeblocks = [sink.get_message(i) for i in range(sink.num_messages())]
        self.assertEqual([len(self._payload(c)) for c in codeblocks], [255, 255])
        self.assertEqual([self._burst_more(c) for c in codeblocks], [True, False])


if __name__ == '__main__':
    gr_unittest.run(qa_tm_frame_generator)
