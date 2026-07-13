#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest
from gnuradio.soarr import ccsds_receiver
import pmt

class qa_ccsds_receiver(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()
        self.receiver = ccsds_receiver()
        self.captured_output = []

    def tearDown(self):
        self.tb = None
        self.receiver = None
        self.captured_output = []

    def _capture_output(self, block):
        original_pub = block.message_port_pub

        def _capture(port, msg):
            self.captured_output.append((port, msg))

        block.message_port_pub = _capture
        return original_pub

    def _restore_output(self, block, original_pub):
        block.message_port_pub = original_pub

    def _decode_header_fields(self, header_bytes):
        value = int.from_bytes(header_bytes, byteorder="big")
        return {
            "tfvn": (value >> 38) & 0x3,
            "bypass_flag": (value >> 37) & 0x1,
            "control_flag": (value >> 36) & 0x1,
            "reserve": (value >> 34) & 0x3,
            "scid": (value >> 24) & 0x3FF,
            "vcid": (value >> 18) & 0x3F,
            "frame_length": (value >> 8) & 0x3FF,
            "frame_sequence_number": value & 0xFF,
        }

    def _build_frame_chunks(self, frame_length, payload_bytes, scid=0x155, vcid=0x12, fsn=0x33, bypass_flag=False, control_flag=False, tfvn=0, reserve=0):
        header = self.receiver.tc_header().build(dict(
            tfvn=tfvn,
            bypass_flag=bypass_flag,
            control_flag=control_flag,
            reserve=reserve,
            scid=scid,
            vcid=vcid,
            frame_length=frame_length,
            frame_sequence_number=fsn,
        ))
        frame = header + payload_bytes
        self.assertEqual(len(frame), frame_length + 1)
        self.assertEqual(len(frame) % 8, 0)
        return [frame[i:i + 8] for i in range(0, len(frame), 8)]

    def _run_receiver_with_chunks(self, chunks, receiver=None):
        if receiver is None:
            receiver = self.receiver

        self.captured_output = []
        original_pub = self._capture_output(receiver)
        original_read = receiver._readInputMsg
        chunk_iter = iter(chunks)

        def _fake_read(_msg):
            return next(chunk_iter, None)

        receiver._readInputMsg = _fake_read
        try:
            dummy_msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, [0] * 8))
            for _ in chunks:
                receiver.receiver(dummy_msg)
        finally:
            receiver._readInputMsg = original_read
            self._restore_output(receiver, original_pub)

        return self.captured_output

    def test_instance(self):
        instance = ccsds_receiver()
        self.assertIsNotNone(instance)
        self.assertEqual(instance.message_type, 0)
        self.assertEqual(instance.field_type, 0)
        self.assertIsNone(instance.scid)
        self.assertIsNone(instance.vcid)

    def test_001_invalid_header_does_not_publish(self):
        payload = bytes([0xAA, 0xBB, 0xCC])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload + bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]),
            tfvn=1,
        )

        captured = self._run_receiver_with_chunks(chunks[:1])
        self.assertEqual(len(captured), 0)
        self.assertFalse(self.receiver.length_found)

    def test_002_valid_frame_is_published_after_complete_length(self):
        payload = bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0, 0xB0])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x155,
            vcid=0x12,
            fsn=0x22,
        )

        captured = self._run_receiver_with_chunks(chunks)

        self.assertEqual(len(captured), 1)
        out_port, out_msg = captured[0]
        self.assertTrue(pmt.eqv(out_port, pmt.intern("out")))

        out_meta = pmt.car(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(pmt.cdr(out_msg)))

        self.assertEqual(len(out_bytes), 16)
        self.assertEqual(out_bytes[:16], b"".join(chunks))
        self.assertEqual(self._decode_header_fields(out_bytes[:5])["frame_length"], 15)
        self.assertEqual(self._decode_header_fields(out_bytes[:5])["frame_sequence_number"], 0x22)
        self.assertEqual(self._decode_header_fields(out_bytes[:5])["scid"], 0x155)
        self.assertEqual(self._decode_header_fields(out_bytes[:5])["vcid"], 0x12)
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_length")))
        self.assertEqual(int(pmt.to_long(pmt.dict_ref(out_meta, pmt.intern("frame_length"), pmt.PMT_NIL))), 16)

    def test_003_scid_filter_rejects_mismatch(self):
        receiver = ccsds_receiver(scid=0x155, vcid=0x12, scid_filter_enable=True, vcid_filter_enable=False)
        payload = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x2AB,
            vcid=0x12,
            fsn=0x44,
        )

        captured = self._run_receiver_with_chunks(chunks, receiver=receiver)
        self.assertEqual(len(captured), 0)
        self.assertFalse(receiver.length_found)

    def test_004_vcid_filter_rejects_mismatch(self):
        receiver = ccsds_receiver(scid=0x155, vcid=0x12, scid_filter_enable=False, vcid_filter_enable=True)
        payload = bytes([0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x155,
            vcid=0x2E,
            fsn=0x45,
        )

        captured = self._run_receiver_with_chunks(chunks, receiver=receiver)
        self.assertEqual(len(captured), 0)
        self.assertFalse(receiver.length_found)

    def test_005_partial_frame_does_not_publish_yet(self):
        payload = bytes([0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x155,
            vcid=0x12,
            fsn=0x22,
        )

        captured = self._run_receiver_with_chunks(chunks[:1])

        self.assertEqual(len(captured), 0)
        self.assertTrue(self.receiver.length_found)
        # frame_length=15 -> 16 actual bytes; one 8-byte codeword (which
        # itself contains the TFPH) has been consumed, 8 remain.
        self.assertEqual(self.receiver.remaining_frame_length, 8)

    def test_006_invalid_reserve_bits_do_not_publish(self):
        payload = bytes([0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x155,
            vcid=0x12,
            fsn=0x55,
            reserve=1,
        )

        captured = self._run_receiver_with_chunks(chunks[:1])
        self.assertEqual(len(captured), 0)
        self.assertFalse(self.receiver.length_found)

    def test_007_tm_mode_dropped_cleanly(self):
        # message_type=1 (TM) is not implemented; the handler must not
        # raise through to the caller, just log and drop.
        receiver = ccsds_receiver(message_type=1)
        chunk = bytes([0x00] * 8)

        captured = self._run_receiver_with_chunks([chunk], receiver=receiver)
        self.assertEqual(len(captured), 0)

    def test_008_encapsulation_field_dropped_cleanly(self):
        # field_type=1 (Encapsulation Field) is not implemented; the
        # handler must not raise through to the caller, just log and drop.
        receiver = ccsds_receiver(field_type=1)
        chunk = bytes([0x00] * 8)

        captured = self._run_receiver_with_chunks([chunk], receiver=receiver)
        self.assertEqual(len(captured), 0)

    def test_009_publish_failure_dropped_cleanly(self):
        payload = bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0, 0xB0])
        chunks = self._build_frame_chunks(
            frame_length=15,
            payload_bytes=payload,
            scid=0x155,
            vcid=0x12,
            fsn=0x22,
        )

        original_read = self.receiver._readInputMsg
        chunk_iter = iter(chunks)

        def _fake_read(_msg):
            return next(chunk_iter, None)

        def _raise_on_publish(port, msg):
            raise RuntimeError("forced publish failure")

        self.receiver._readInputMsg = _fake_read
        self.receiver.message_port_pub = _raise_on_publish
        try:
            dummy_msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(8, [0] * 8))
            for _ in chunks:
                result = self.receiver.receiver(dummy_msg)
        finally:
            self.receiver._readInputMsg = original_read

        self.assertIsNone(result)


if __name__ == '__main__':
    gr_unittest.run(qa_ccsds_receiver)
