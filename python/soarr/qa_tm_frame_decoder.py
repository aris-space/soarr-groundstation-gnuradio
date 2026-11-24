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

from gnuradio.soarr import encapsulation_header, encapsulation_parser, tm_frame_decoder, tm_frame_generator

try:
    import satellites
except ImportError:  # the TC side needs no gr-satellites
    satellites = None

IDLE = 0xE0  # CCSDS 133.1-B: a one-byte Encapsulation Idle Packet
NO_PACKET_START = 0x7FF  # 132.0-B-3 4.1.2.7.6.4
ONLY_IDLE_DATA = 0x7FE   # 132.0-B-3 4.1.2.7.6.5


def _crc16(data):
    """CRC-16/CCITT-FALSE bit by bit (132.0-B-3 4.1.6.2)."""
    crc = 0xFFFF
    for byte in data:
        for bit in range(7, -1, -1):
            feedback = ((crc >> 15) & 1) ^ ((byte >> bit) & 1)
            crc = (crc << 1) & 0xFFFF
            if feedback:
                crc ^= 0x1021
    return crc


def _frame(data_field, fhp, scid=0, vcid=0, mc_count=0, vc_count=0, ocf=None, secondary=None, fecf=False,
           sync=0, version=0, secondary_version=0):
    """A TM Transfer Frame built field by field (132.0-B-3 4.1), independent
    of the TM Frame Generator."""
    first = (version << 14) | (scid << 4) | (vcid << 1) | int(ocf is not None)
    status = (int(secondary is not None) << 15) | (sync << 14) | (0b11 << 11) | fhp
    frame = first.to_bytes(2, "big") + bytes([mc_count, vc_count]) + status.to_bytes(2, "big")
    if secondary is not None:
        frame += bytes([(secondary_version << 6) | len(secondary)]) + secondary
    frame += data_field
    if ocf is not None:
        frame += ocf
    if fecf:
        frame += _crc16(frame).to_bytes(2, "big")
    return frame


def _packet(length, start=0):
    """An encapsulation packet (version 111, protocol ID 111) of length
    bytes in total, header included; length is at least 2."""
    if length <= 0xFF:
        header = bytes([0xFD, length])
    elif length <= 0xFFFF:
        header = bytes([0xFE, 0x00]) + length.to_bytes(2, "big")
    else:
        header = bytes([0xFF, 0x00, 0x00, 0x00]) + length.to_bytes(4, "big")
    return header + bytes((start + i) % 251 for i in range(length - len(header)))


def _long_header_packet(length, start=0):
    """An encapsulation packet with the 8-byte header (length of length 11)."""
    return bytes([0xFF, 0x00, 0x00, 0x00]) + length.to_bytes(4, "big") + bytes(
        (start + i) % 251 for i in range(length - 8))


def _space_packet(data_length, apid=0x64):
    """A Space Packet (CCSDS 133.0-B-2, version 000)."""
    return (bytes([0x08 | (apid >> 8), apid & 0xFF, 0xC0, 0x00]) + (data_length - 1).to_bytes(2, "big")
            + bytes(i % 256 for i in range(data_length)))


class qa_tm_frame_decoder(gr_unittest.TestCase):

    def _pdu(self, data, meta=None):
        meta = pmt.make_dict() if meta is None else meta
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def _meta(self, msg, key):
        value = pmt.dict_ref(pmt.car(msg), pmt.intern(key), pmt.PMT_NIL)
        if pmt.eq(value, pmt.PMT_NIL):
            return None
        if pmt.is_u8vector(value):
            return bytes(pmt.u8vector_elements(value))
        if pmt.is_uint64(value):
            return pmt.to_uint64(value)
        return pmt.to_long(value)

    def _decode(self, block, frames):
        """Feed the frames (bytes or PDUs) to block; what it published, by port."""
        published = {"out": [], "space_packets": [], "ocf": []}
        block.message_port_pub = lambda port, m: published[pmt.symbol_to_string(port)].append(m)
        for frame in frames:
            block.decode_frame(self._pdu(frame) if isinstance(frame, bytes) else frame)
        return published

    def _generate(self, generator, packet, meta=None):
        """The frames the TM Frame Generator builds for one packet."""
        frames = []
        generator.message_port_pub = lambda port, m: frames.append(self._payload(m))
        generator.build_frames(self._pdu(packet, meta))
        return frames

    def _packets(self, published, port="out"):
        return [self._payload(m) for m in published[port]]

    # A short packet in one idle-filled frame comes out whole, with the
    # frame's metadata and its SCID, VCID and frame counts.
    def test_001_one_packet_in_one_frame(self):
        packet = _packet(10)
        (frame,) = self._generate(tm_frame_generator(scid=0x2AB, vcid=5), packet)
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("frame_id"), pmt.from_long(7))
        published = self._decode(tm_frame_decoder(), [self._pdu(frame, meta)])
        self.assertEqual(self._packets(published), [packet])
        (msg,) = published["out"]
        self.assertEqual([self._meta(msg, k) for k in ("scid", "vcid", "mc_frame_count", "vc_frame_count", "frame_id")],
                         [0x2AB, 5, 0, 0, 7])
        self.assertIsNone(self._meta(msg, "secondary_header"))
        self.assertIsNone(self._meta(msg, "ocf"))
        self.assertEqual(published["space_packets"] + published["ocf"], [])

    # Packets follow each other in the data field; idle packets between
    # and after them are skipped.
    def test_002_several_packets_in_one_frame(self):
        p1, p2 = _packet(5, 1), _packet(7, 2)
        frame = _frame(p1 + bytes([IDLE]) + p2 + bytes([IDLE]) * 7, fhp=0)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [frame])), [p1, p2])

    # Packets split across frames come back whole: with idle fill in the
    # last frame and with an exact fit, one after the other.
    def test_003_packet_split_across_frames(self):
        generator, decoder = tm_frame_generator(frame_length=223), tm_frame_decoder()
        p1, p2, p3 = _packet(500, 1), _packet(2 * 217, 2), _packet(217, 3)
        frames = self._generate(generator, p1) + self._generate(generator, p2) + self._generate(generator, p3)
        self.assertEqual(len(frames), 6)
        published = self._decode(decoder, frames)
        self.assertEqual(self._packets(published), [p1, p2, p3])
        self.assertEqual([self._meta(m, "vc_frame_count") for m in published["out"]], [0, 3, 5])

    # A packet header itself can be cut by the frame boundary.
    def test_004_packet_header_split_across_frames(self):
        p1, p2 = _packet(17, 1), _long_header_packet(30, 2)
        f1 = _frame(p1 + p2[:3], fhp=0, vc_count=0)
        f2 = _frame(p2[3:23], fhp=NO_PACKET_START, vc_count=1)
        f3 = _frame(p2[23:] + bytes([IDLE]) * 13, fhp=7, vc_count=2)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [f1, f2, f3])), [p1, p2])

    # An idle-only frame carries nothing; the end of a packet whose start
    # wasn't received is dropped, the packet after it kept.
    def test_005_idle_frames_and_continuations_without_start(self):
        p1 = _packet(8, 1)
        frames = [_frame(bytes([IDLE]) * 20, fhp=ONLY_IDLE_DATA, vc_count=0),
                  _frame(bytes(range(20)), fhp=NO_PACKET_START, vc_count=1),
                  _frame(bytes(5) + p1 + bytes([IDLE]) * 7, fhp=5, vc_count=2)]
        published = self._decode(tm_frame_decoder(), frames)
        self.assertEqual(self._packets(published), [p1])

        # A decoder started in the middle of a packet waits for the next one.
        p2 = _packet(500, 2)
        later = self._generate(tm_frame_generator(), p2)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), later[1:] + later)), [p2])

    # A lost frame (a gap in the VC frame count) discards the packet in
    # progress; the next packet comes through.
    def test_006_lost_frame_discards_the_packet(self):
        generator = tm_frame_generator(frame_length=223)
        p1, p2 = _packet(500, 1), _packet(300, 2)
        f1, f2 = self._generate(generator, p1), self._generate(generator, p2)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [f1[0], f1[2]] + f2)), [p2])

        # Also across the count wrap: 255 -> 1 is a gap, 255 -> 0 isn't.
        p3 = _packet(30, 3)
        frames = [_frame(p3[:20], fhp=0, vc_count=255), _frame(p3[20:] + bytes([IDLE]) * 10, fhp=10, vc_count=1)]
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), frames)), [])
        frames[1] = _frame(p3[20:] + bytes([IDLE]) * 10, fhp=10, vc_count=0)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), frames)), [p3])

    # The First Header Pointer must agree with the packet in progress;
    # if it doesn't, the packet is discarded and decoding continues at it.
    def test_007_first_header_pointer_contradicting_the_packet(self):
        p1, p2 = _packet(30, 1), _packet(6, 2)
        start = _frame(p1[:20], fhp=0, vc_count=0)
        early = _frame(p1[20:24] + p2 + bytes([IDLE]) * 10, fhp=4, vc_count=1)  # p1 needs 10 more bytes
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [start, early])), [p2])
        late = _frame(p1[20:] + bytes([IDLE]) * 4 + p2, fhp=14, vc_count=1)  # p1 ends at 10
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [start, late])), [p2])
        unmarked = _frame(p1[20:] + p2 + bytes([IDLE]) * 4, fhp=NO_PACKET_START, vc_count=1)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [start, unmarked])), [])
        exact = _frame(p1[20:] + p2 + bytes([IDLE]) * 4, fhp=10, vc_count=1)
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(), [start, exact])), [p1, p2])

    # The secondary header is read from its flag and published; with
    # fecf, a frame with a wrong FECF is dropped.
    def test_008_secondary_header_and_fecf(self):
        generator = tm_frame_generator(frame_length=40, secondary_header=True, secondary_header_data="AABBCC",
                                       fecf=True)
        packet = _packet(60)
        frames = self._generate(generator, packet)
        published = self._decode(tm_frame_decoder(fecf=True), frames)
        self.assertEqual(self._packets(published), [packet])
        self.assertEqual(self._meta(published["out"][0], "secondary_header"), bytes.fromhex("AABBCC"))

        broken = bytearray(frames[1])
        broken[20] ^= 0x01
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(fecf=True), [frames[0], bytes(broken)])), [])
        short = _packet(5)
        (frame,) = self._generate(generator, short)
        bad_crc = frame[:-1] + bytes([frame[-1] ^ 0xFF])
        self.assertEqual(self._packets(self._decode(tm_frame_decoder(fecf=True), [bad_crc])), [])

    # Every frame with an OCF publishes it, also without a packet start
    # or with only idle data; packets carry the OCF of their first frame.
    def test_009_ocf_of_every_frame(self):
        generator = tm_frame_generator(frame_length=223, ocf=True, ocf_value=0xA1B2C3D4)
        packet = _packet(500)
        frames = self._generate(generator, packet)
        frames.append(_frame(bytes([IDLE]) * 10, fhp=ONLY_IDLE_DATA, vc_count=3, mc_count=3,
                             ocf=bytes.fromhex("01020304")))
        published = self._decode(tm_frame_decoder(), frames)
        self.assertEqual(self._packets(published, "ocf"), [bytes.fromhex("A1B2C3D4")] * 3 + [bytes.fromhex("01020304")])
        self.assertEqual([self._meta(m, "vc_frame_count") for m in published["ocf"]], [0, 1, 2, 3])
        self.assertEqual([self._meta(m, "scid") for m in published["ocf"]], [0] * 4)
        self.assertEqual(self._packets(published), [packet])
        self.assertEqual(self._meta(published["out"][0], "ocf"), 0xA1B2C3D4)

    # Space Packets come out on their own port, idle ones (APID all ones)
    # skipped; a frame can mix them with encapsulation packets.
    def test_010_space_packets(self):
        packet = _space_packet(300)
        published = self._decode(tm_frame_decoder(), self._generate(tm_frame_generator(), packet))
        self.assertEqual(self._packets(published, "space_packets"), [packet])
        self.assertEqual(published["out"], [])

        idle, s1, e1 = _space_packet(3, apid=0x7FF), _space_packet(4), _packet(5)
        frame = _frame(idle + s1 + e1 + bytes([IDLE]) * 3, fhp=0)
        published = self._decode(tm_frame_decoder(), [frame])
        self.assertEqual(self._packets(published, "space_packets"), [s1])
        self.assertEqual(self._packets(published), [e1])

    # Each virtual channel keeps its own packet in progress and count.
    def test_011_interleaved_virtual_channels(self):
        generator = tm_frame_generator(frame_length=223)
        p1, p2 = _packet(500, 1), _packet(400, 2)
        f1 = self._generate(generator, p1, pmt.dict_add(pmt.make_dict(), pmt.intern("vcid"), pmt.from_long(1)))
        f2 = self._generate(generator, p2, pmt.dict_add(pmt.make_dict(), pmt.intern("vcid"), pmt.from_long(2)))
        interleaved = [f1[0], f2[0], f1[1], f2[1], f1[2]]
        published = self._decode(tm_frame_decoder(), interleaved)
        self.assertEqual(self._packets(published), [p2, p1])
        self.assertEqual([self._meta(m, "vcid") for m in published["out"]], [2, 1])

    # With a filter on, frames of another SCID or VCID give nothing, not
    # even their OCF.
    def test_012_scid_and_vcid_filters(self):
        p1, ocf = _packet(5), bytes(4)
        frames = [_frame(p1 + bytes([IDLE]) * 5, fhp=0, scid=5, vcid=1, ocf=ocf),
                  _frame(p1 + bytes([IDLE]) * 5, fhp=0, scid=6, vcid=1, ocf=ocf),
                  _frame(p1 + bytes([IDLE]) * 5, fhp=0, scid=5, vcid=2, ocf=ocf)]
        for decoder, expected in ((tm_frame_decoder(), [(5, 1), (6, 1), (5, 2)]),
                                  (tm_frame_decoder(scid_filter_enable=True, scid=5), [(5, 1), (5, 2)]),
                                  (tm_frame_decoder(vcid_filter_enable=True, vcid=1), [(5, 1), (6, 1)]),
                                  (tm_frame_decoder(scid_filter_enable=True, scid=5, vcid_filter_enable=True,
                                                    vcid=2), [(5, 2)])):
            published = self._decode(decoder, frames)
            for port in ("out", "ocf"):
                self.assertEqual([(self._meta(m, "scid"), self._meta(m, "vcid")) for m in published[port]], expected)

        for kwargs in ({"scid": 0x400}, {"scid": -1}, {"vcid": 8}, {"vcid": -1}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                tm_frame_decoder(**kwargs)

    def test_013_malformed_input_is_dropped(self):
        p1 = _packet(5)
        good = _frame(p1 + bytes([IDLE]) * 5, fhp=0, vc_count=9)
        for msg in (pmt.intern("not a pdu"), pmt.cons(pmt.intern("no dict"), pmt.init_u8vector(1, [0])),
                    pmt.cons(pmt.make_dict(), pmt.intern("not bytes")), self._pdu(bytes(5)),
                    self._pdu(_frame(p1, fhp=0, version=1)),
                    self._pdu(_frame(p1, fhp=0, secondary=bytes(2), secondary_version=1)),
                    self._pdu(_frame(b"", fhp=0, ocf=bytes(4))[:8]),           # too short for its OCF
                    self._pdu(_frame(bytes(10), fhp=100)),                      # pointer beyond the data field
                    self._pdu(_frame(bytes([0x40]) + bytes(9), fhp=0)),          # version 010: no length field
                    self._pdu(_frame(bytes([0xFD, 0x01]) + bytes(8), fhp=0))):  # length shorter than its header
            published = self._decode(tm_frame_decoder(), [msg])
            self.assertEqual(sum(len(v) for v in published.values()), 0, str(msg))

        # Sync flag 1: the OCF still comes out, the data field doesn't.
        published = self._decode(tm_frame_decoder(), [_frame(p1 + bytes(5), fhp=0, sync=1, ocf=bytes(4))])
        self.assertEqual((len(published["out"]), len(published["ocf"])), (0, 1))

        # An undelimitable packet drops the rest of its frame only.
        decoder = tm_frame_decoder()
        published = self._decode(decoder, [_frame(p1 + bytes([0x40]) + bytes(4), fhp=0, vc_count=8), good])
        self.assertEqual(self._packets(published), [p1, p1])

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        decoder.message_port_pub = _raise
        decoder.decode_frame(self._pdu(good))  # must not raise

    # The whole chain: encapsulation header, frames, Reed-Solomon coding
    # and back, through the Encapsulation Parser to the original payload.
    @unittest.skipIf(satellites is None, "gr-satellites is not installed (needed for TM Reed-Solomon)")
    def test_014_round_trip_through_reed_solomon(self):
        payloads = [bytes(range(200)) * 3, b"short", bytes(217)]
        tb = gr.top_block()
        chain = [encapsulation_header(), tm_frame_generator(frame_length=223), satellites.encode_rs(True, 1),
                 satellites.decode_rs(True, 1), tm_frame_decoder(), encapsulation_parser()]
        sink = blocks.message_debug()
        for a, b in zip(chain, chain[1:]):
            tb.msg_connect((a, "out"), (b, "in"))
        tb.msg_connect((chain[-1], "out"), (sink, "store"))
        tb.start()
        for payload in payloads:
            chain[0].to_basic_block()._post(pmt.intern("in"), self._pdu(payload))
        deadline = time.time() + 3.0
        while sink.num_messages() < len(payloads) and time.time() < deadline:
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        self.assertEqual([self._payload(sink.get_message(i)) for i in range(sink.num_messages())], payloads)


if __name__ == '__main__':
    gr_unittest.run(qa_tm_frame_decoder)
