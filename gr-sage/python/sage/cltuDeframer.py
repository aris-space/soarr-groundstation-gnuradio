#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import struct

import numpy as np
import pmt
from gnuradio import gr


class cltuDeframer(gr.basic_block):
    """Find CLTU frames in a uint8 stream and publish the payload as a PDU."""

    def __init__(self, start_sequence:int=0xEB90, tail_sequence:int=0xC5C5C5C5C5C5C579, payload_bytes:int=8, threshold:int=2, input_packed:bool=True, output_packed:bool=True):
        if payload_bytes <= 0:
            raise ValueError("payload_bytes must be positive")
        if threshold < 0:
            raise ValueError("threshold must be non-negative")

        gr.basic_block.__init__(self, name="cltuDeframer", in_sig=[np.uint8], out_sig=None)

        self.message_port_register_out(pmt.intern("out"))

        self.start_sequence = start_sequence
        self.tail_sequence = tail_sequence
        self.data_length = payload_bytes
        self.threshold = threshold
        self.input_packed = input_packed
        self.output_packed = output_packed

        self._buffer = bytearray()
        # partial byte state when input_packed == False
        self._partial_byte = 0
        self._partial_count = 0
        self._start_bytes = struct.pack("!H", self.start_sequence & 0xFFFF)
        self._tail_bytes = struct.pack("!Q", self.tail_sequence & 0xFFFFFFFFFFFFFFFF)
        self._frame_length = len(self._start_bytes) + self.data_length + len(self._tail_bytes)

    def _bit_errors(self, left, right):
        return sum((l ^ r).bit_count() for l, r in zip(left, right))

    def _matches_frame(self, frame):
        if len(frame) != self._frame_length:
            return False

        if self._bit_errors(frame[:len(self._start_bytes)], self._start_bytes) > self.threshold:
            return False

        if self._bit_errors(frame[-len(self._tail_bytes):], self._tail_bytes) > self.threshold:
            return False

        return True

    def _publish_payload(self, payload_bytes):
        if not self.output_packed:
            unpacked = []
            for value in payload_bytes:
                for shift in range(7, -1, -1):
                    unpacked.append((value >> shift) & 1)
            payload_bytes = bytes(unpacked)

        try:
            hex_str = payload_bytes.hex()
        except Exception:
            hex_str = repr(payload_bytes)
        self.logger.debug(f"Publishing payload: {hex_str}")
        out_msg = pmt.cons(pmt.make_dict(), pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.message_port_pub(pmt.intern("out"), out_msg)

    def _try_extract_frames(self):
        while len(self._buffer) >= self._frame_length:
            frame_start = None
            limit = len(self._buffer) - self._frame_length + 1

            for offset in range(limit):
                candidate = self._buffer[offset:offset + self._frame_length]
                if self._matches_frame(candidate):
                    frame_start = offset
                    break

            if frame_start is None:
                keep = self._frame_length - 1
                if len(self._buffer) > keep:
                    del self._buffer[:-keep]
                return

            frame_end = frame_start + self._frame_length
            payload_start = frame_start + len(self._start_bytes)
            payload_end = payload_start + self.data_length
            payload = bytes(self._buffer[payload_start:payload_end])
            self._publish_payload(payload)
            del self._buffer[:frame_end]

    def process_bytes(self, incoming):
        if not incoming:
            return

        if self.input_packed:
            # incoming is a sequence of bytes
            self._buffer.extend(incoming)
        else:
            # incoming is a sequence of bits (0/1) as uint8 values; pack MSB-first into bytes
            for b in incoming:
                bit = int(b) & 1
                self._partial_byte = (self._partial_byte << 1) | bit
                self._partial_count += 1
                if self._partial_count == 8:
                    self._buffer.append(self._partial_byte)
                    self._partial_byte = 0
                    self._partial_count = 0

        self._try_extract_frames()

    def general_work(self, input_items, output_items):
        if not input_items:
            return 0

        incoming = bytes(input_items[0])
        self.process_bytes(incoming)

        self.consume_each(len(incoming))
        return 0

